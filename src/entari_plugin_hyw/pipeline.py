import asyncio
import html
import json
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from openai import AsyncOpenAI

from .search import SearchService
from .image_cache import get_cached_images
from .prompts import (
    SUMMARY_SP,
    INSTRUCT_SP,
)

@asynccontextmanager
async def _null_async_context():
    yield None


class ProcessingPipeline:
    """
    Core pipeline (vision -> instruct/search -> agent).
    """

    def __init__(self, config: Any):
        self.config = config
        self.search_service = SearchService(config)
        self.client = AsyncOpenAI(base_url=self.config.base_url, api_key=self.config.api_key)
        self.all_web_results = [] # Cache for search results
        self.current_mode = "standard"  # standard | agent
        # Global ID counter for all types (unified numbering)
        self.global_id_counter = 0
        # Background tasks for async image search (not blocking agent)
        self._image_search_tasks: List[asyncio.Task] = []
        self._search_error: Optional[str] = None # Track critical search errors

        self.web_search_tool = {
            "type": "function",
            "function": {
                "name": "internal_web_search",
                "description": "Search the web for text.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
        self.crawl_page_tool = {
            "type": "function",
            "function": {
                "name": "crawl_page",
                "description": "使用 Crawl4AI 抓取网页并返回 Markdown 文本。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                    },
                    "required": ["url"],
                },
            },
        }
        self.refuse_answer_tool = {
            "type": "function",
            "function": {
                "name": "refuse_answer",
                "description": "拒绝回答问题。当用户问题涉及敏感、违规、不适宜内容时调用此工具，立即终止流程并返回拒绝回答的图片。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "拒绝回答的原因（展示给用户）"},
                    },
                    "required": [],
                },
            },
        }
        # Flag to indicate refuse_answer was called
        self._should_refuse = False
        self._refuse_reason = ""

    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict],
        model_name: str = None,
        images: List[str] = None,
        vision_model_name: str = None,
        selected_vision_model: str = None,
    ) -> Dict[str, Any]:
        """
        New Pipeline Flow:
        1) Instruct: Images go directly here, decides web_search/crawl_page/refuse.
        2) Auto-Fetch: Automatically fetch first 4 search result pages.
        3) Screenshot: Render fetched pages as screenshots.
        4) Summary: Receives user images + page screenshots for final answer.
        """
        start_time = time.time()
        stats = {"start_time": start_time, "tool_calls_count": 0}
        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        active_model = model_name or self.config.model_name

        current_history = conversation_history
        # Reset globals
        self.all_web_results = []
        self.global_id_counter = 0
        self._should_refuse = False
        self._refuse_reason = ""
        self._image_search_tasks = []

        try:
            logger.info(f"Pipeline: Starting workflow for '{user_input}' using {active_model}")
            
            trace: Dict[str, Any] = {
                "instruct": None,
                "search": None,
                "fetch": None,
                "summary": None,
            }

            # --- 1. Instruct Stage (with images if provided) ---
            instruct_start = time.time()
            instruct_model = getattr(self.config, "instruct_model_name", None) or active_model
            instruct_text, search_payloads, instruct_trace, instruct_usage, search_time = await self._run_instruct_stage(
                user_input=user_input,
                images=images,  # Pass images directly to instruct
                model=instruct_model,
            )
            
            # Check refuse
            if self._should_refuse:
                return {
                     "llm_response": "",
                     "structured_response": {},
                     "stats": stats,
                     "model_used": active_model,
                     "conversation_history": current_history,
                     "refuse_answer": True,
                     "refuse_reason": self._refuse_reason
                }
            
            # Check for critical search errors
            if self._search_error:
                return {
                     "llm_response": "",
                     "structured_response": {},
                     "stats": stats,
                     "model_used": active_model,
                     "conversation_history": current_history,
                     "refuse_answer": True,
                     "refuse_reason": f"搜索服务异常: {self._search_error} 请联系管理员。"
                }

            usage_totals["input_tokens"] += instruct_usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += instruct_usage.get("output_tokens", 0)
            
            instruct_cost = 0.0
            i_in_price = float(getattr(self.config, "instruct_input_price", None) or getattr(self.config, "input_price", 0.0) or 0.0)
            i_out_price = float(getattr(self.config, "instruct_output_price", None) or getattr(self.config, "output_price", 0.0) or 0.0)
            if i_in_price > 0 or i_out_price > 0:
                instruct_cost = (instruct_usage.get("input_tokens", 0) / 1_000_000 * i_in_price) + (instruct_usage.get("output_tokens", 0) / 1_000_000 * i_out_price)
            
            instruct_trace["cost"] = instruct_cost
            trace["instruct"] = instruct_trace

            # --- 2. Auto-Fetch Stage (Automatically fetch first 4 search results) ---
            fetch_start = time.time()
            fetch_trace = {}
            page_screenshots: List[str] = []  # Base64 screenshots of fetched pages
            
            fetch_urls = []
            search_items = [r for r in self.all_web_results if r.get("_type") == "search"]
            if search_items:
                # Group search results by query
                query_groups = {}
                for r in search_items:
                    q = r.get("query", "default")
                    if q not in query_groups:
                        query_groups[q] = []
                    query_groups[q].append(r)
                
                raw_fetch_urls = []
                # If multiple queries, take top 3 from each
                if len(query_groups) > 1:
                    logger.info(f"Pipeline: Multiple search queries detected ({len(query_groups)}). Taking top 3 from each.")
                    for q, items in query_groups.items():
                        for item in items[:3]:
                            if item.get("url"):
                                raw_fetch_urls.append(item.get("url"))
                else:
                    # Single query, take top 8
                    raw_fetch_urls = [r.get("url") for r in search_items[:8] if r.get("url")]
                
                # Deduplicate while preserving order and filter blocked domains
                final_fetch_urls = []
                blocked_domains = getattr(self.config, "fetch_blocked_domains", None)
                if blocked_domains is None:
                    blocked_domains = ["wikipedia.org", "csdn.net", "sohu.com", "sogou.com"]
                if isinstance(blocked_domains, str):
                    blocked_domains = [d.strip() for d in blocked_domains.split(",")]
                
                for url in raw_fetch_urls:
                    if url and url not in final_fetch_urls:
                        # Check blocklist
                        if any(domain in url.lower() for domain in blocked_domains):
                            continue
                        final_fetch_urls.append(url)
                
                fetch_urls = final_fetch_urls

            # Check if search was performed but no URLs were available for fetching
            has_search_call = False
            if instruct_trace and "tool_calls" in instruct_trace:
                has_search_call = any(tc.get("name") in ["web_search", "internal_web_search"] for tc in instruct_trace["tool_calls"])
            
            if has_search_call and not fetch_urls:
                 return {
                     "llm_response": "",
                     "structured_response": {},
                     "stats": stats,
                     "model_used": active_model,
                     "conversation_history": current_history,
                     "refuse_answer": True,
                     "refuse_reason": "搜索结果为空或全部被过滤，无法生成回答。"
                 }

            if fetch_urls:
                logger.info(f"Pipeline: Auto-fetching up to {len(fetch_urls)} pages (keeping fastest 5): {fetch_urls}")
                
                # Execute fetch and get screenshots
                await self._run_auto_fetch_with_screenshots(fetch_urls)

                fetch_trace = {
                    "model": "Auto",
                    "urls_fetched": fetch_urls,
                    "time": time.time() - fetch_start,
                    "cost": 0.0,
                }
                trace["fetch"] = fetch_trace

            # Always collect screenshots from ALL page results (search auto-fetch + direct URL crawl)
            fetch_items = [r for r in self.all_web_results if r.get("_type") == "page"]
            for r in fetch_items:
                if r.get("screenshot_b64"):
                    page_screenshots.append(r["screenshot_b64"])
            
            if fetch_trace:
                fetch_trace["screenshots_count"] = len(page_screenshots)

            # --- 3. Summary Stage (with user images + page screenshots only) ---
            summary_start = time.time()
            summary_model = active_model
            
            # Combine user images and page screenshots for summary
            all_summary_images: List[str] = []
            if images:
                all_summary_images.extend(images)
            all_summary_images.extend(page_screenshots)
            
            summary_content, summary_usage, summary_trace_info = await self._run_summary_stage(
                user_input=user_input,
                images=all_summary_images if all_summary_images else None,
                has_page_screenshots=bool(page_screenshots),
                model=summary_model
            )
            
            usage_totals["input_tokens"] += summary_usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += summary_usage.get("output_tokens", 0)
            
            summary_cost = 0.0
            s_in_price = float(getattr(self.config, "input_price", 0.0) or 0.0)
            s_out_price = float(getattr(self.config, "output_price", 0.0) or 0.0)
            if s_in_price > 0 or s_out_price > 0:
                 summary_cost = (summary_usage.get("input_tokens", 0) / 1_000_000 * s_in_price) + (summary_usage.get("output_tokens", 0) / 1_000_000 * s_out_price)

            trace["summary"] = {
                "model": summary_model,
                "system_prompt": summary_trace_info.get("prompt", ""),
                "output": summary_content,
                "usage": summary_usage,
                "time": time.time() - summary_start,
                "cost": summary_cost,
                "images_count": len(all_summary_images)
            }

            # --- Result Assembly ---
            stats["total_time"] = time.time() - start_time
            structured = self._parse_tagged_response(summary_content)
            final_content = structured.get("response") or summary_content
            
            billing_info = {
                "input_tokens": usage_totals["input_tokens"],
                "output_tokens": usage_totals["output_tokens"],
                "total_cost": instruct_cost + summary_cost
            }
            
            # Build stages_used
            stages_used = []
            
            # Get page info
            fetch_items = [r for r in self.all_web_results if r.get("_type") == "page"]
            crawled_pages_ui = []
            for r in fetch_items:
                domain = ""
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(r.get("url", "")).netloc
                except: pass
                crawled_pages_ui.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "favicon_url": f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
                })
            
            # Extract images from pages
            extracted_images = []
            seen_imgs = set()
            junk_keywords = ["icon", "logo", "badge", "avatar", "button", "social", "footer", "header", "banner", "license", "by-nc", "hosted_by", "pixel", "tracker", "ad", "ads", "advert", "promotion", "shop", "store", "group", "join", "qr", "qrcode", "weibo", "weixin", "douyin", "xiaohongshu", "bilibili", "official", "follow", "subscribe", "app"]
            
            for r in fetch_items:
                if "images" in r:
                    for img_url in r["images"]:
                        if img_url not in seen_imgs:
                            # Filter junk images
                            lower_url = img_url.lower()
                            if any(k in lower_url for k in junk_keywords):
                                continue
                                
                            extracted_images.append({
                                "title": r.get("title", "Image"),
                                "url": img_url,
                                "thumbnail": img_url,
                                "domain": r.get("domain", "")
                            })
                            seen_imgs.add(img_url)
                
            # Instruct Stage (with crawled pages and images)
            if trace.get("instruct"):
                i = trace["instruct"]
                # Total time = instruct + search + fetch (until summary starts)
                instruct_total_time = (i.get("time", 0) or 0) + search_time
                if trace.get("fetch"):
                    instruct_total_time += trace["fetch"].get("time", 0)
                
                stages_used.append({
                    "name": "Instruct",
                    "model": i.get("model"),
                    "icon_config": "openai",
                    "provider": "Instruct",
                    "time": instruct_total_time,
                    "cost": i.get("cost", 0),
                    "has_images": bool(images),
                    "crawled_pages": crawled_pages_ui,  # Add crawled pages here
                    "image_references": extracted_images[:9]  # Add images here
                })
            
            # Summary Stage
            if trace.get("summary"):
                s = trace["summary"]
                stages_used.append({
                    "name": "Summary",
                    "model": s.get("model"),
                    "icon_config": "openai",
                    "provider": "Summary",
                    "time": s.get("time", 0),
                    "cost": s.get("cost", 0),
                    "images_count": s.get("images_count", 0)
                })

            # Construct final trace markdown
            trace_markdown = self._render_trace_markdown(trace)
            
            # Update history
            current_history.append({"role": "user", "content": user_input or "..."})
            current_history.append({"role": "assistant", "content": final_content})

            # Schedule async cache task (fire and forget - doesn't block return)
            cache_data = {
                "user_input": user_input,
                "trace": trace,
                "trace_markdown": trace_markdown,
                "page_screenshots": page_screenshots,
                "final_content": final_content,
                "stages_used": stages_used,
            }
            asyncio.create_task(self._cache_run_async(cache_data))

            return {
                "llm_response": final_content,
                "structured_response": structured,
                "stats": stats,
                "model_used": active_model,
                "conversation_history": current_history,
                "trace_markdown": trace_markdown,
                "billing_info": billing_info,
                "stages_used": stages_used,
            }

        except Exception as e:
            logger.exception("Pipeline Critical Failure")
            # Cancel all background image tasks on error
            if hasattr(self, '_image_search_tasks') and self._image_search_tasks:
                for task in self._image_search_tasks:
                    if not task.done(): task.cancel()
                try:
                    await asyncio.wait(self._image_search_tasks, timeout=0.1)
                except Exception: pass
                self._image_search_tasks = []

            return {
                "llm_response": f"I encountered a critical error: {e}",
                "stats": stats,
                "error": str(e),
            }

    def _parse_tagged_response(self, text: str) -> Dict[str, Any]:
        """Parse response and auto-infer references from citations and markdown images.
        """
        parsed = {"response": "", "references": [], "page_references": [], "image_references": [], "flow_steps": []}
        if not text:
            return parsed

        import re
        
        # 1. Strip trailing reference/source list
        body_text = text
        ref_list_pattern = re.compile(r'(?:\n\s*|^)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources|参考资料|引用)[\s\S]*$', re.IGNORECASE | re.MULTILINE)
        body_text = ref_list_pattern.sub('', body_text)
        
        remaining_text = body_text.strip()
        
        # 2. Unwrap JSON if necessary
        try:
            if remaining_text.strip().startswith("{") and "action" in remaining_text:
                data = json.loads(remaining_text)
                if isinstance(data, dict) and "action_input" in data:
                    remaining_text = data["action_input"]
        except Exception:
            pass

        # 3. Identify all citations [N] and direct markdown images ![]()
        cited_ids = []
        body_pattern = re.compile(r'\[(\d+)\]')
        for match in body_pattern.finditer(remaining_text):
            try:
                cited_ids.append(int(match.group(1)))
            except ValueError: pass

        # Also find direct URLs in ![]() 
        direct_image_urls = []
        img_pattern = re.compile(r'!\[.*?\]\((.*?)\)')
        for match in img_pattern.finditer(remaining_text):
            url = match.group(1).strip()
            if url and not url.startswith('['): # Not a [N] citation
                direct_image_urls.append(url)

        # 4. Build Citation Maps and Reference Lists
        unified_id_map = {}
        # Keep track of what we've already added to avoid duplicates
        seen_urls = set()
        
        # id_order needs to be unique and preserve appearance order
        id_order = []
        for id_val in cited_ids:
            if id_val not in id_order:
                id_order.append(id_val)

        # Process [N] citations first to determine numbering
        for old_id in id_order:
            result_item = next((r for r in self.all_web_results if r.get("_id") == old_id), None)
            if not result_item: continue
            
            url = result_item.get("url", "")
            item_type = result_item.get("_type", "")
            
            entry = {
                "title": result_item.get("title", ""),
                "url": url,
                "domain": result_item.get("domain", "")
            }
            
            if item_type == "search":
                parsed["references"].append(entry)
                unified_id_map[old_id] = len(parsed["references"]) + len(parsed["page_references"])
                seen_urls.add(url)
            elif item_type == "page":
                parsed["page_references"].append(entry)
                unified_id_map[old_id] = len(parsed["references"]) + len(parsed["page_references"])
                seen_urls.add(url)
            elif item_type == "image":
                 entry["thumbnail"] = result_item.get("thumbnail", "")
                 if url not in seen_urls:
                    parsed["image_references"].append(entry)
                    seen_urls.add(url)
                 # Note: Images cited as [N] might be used in text like ![...]([N])
                 # We'll handle this in replacement

        # Now handle direct image URLs from ![]() that weren't cited as [N]
        for url in direct_image_urls:
            if url in seen_urls: continue
            # Find in all_web_results
            result_item = next((r for r in self.all_web_results if (r.get("url") == url or r.get("image") == url) and r.get("_type") == "image"), None)
            if result_item:
                entry = {
                    "title": result_item.get("title", ""),
                    "url": url,
                    "domain": result_item.get("domain", ""),
                    "thumbnail": result_item.get("thumbnail", "")
                }
                parsed["image_references"].append(entry)
                seen_urls.add(url)

        # 5. Replacement Logic
        # Define image replacement map separately to handle ![...]([N])
        image_url_map = {} # old_id -> raw_url
        for old_id in id_order:
            item = next((r for r in self.all_web_results if r.get("_id") == old_id), None)
            if item and item.get("_type") == "image":
                image_url_map[old_id] = item.get("url", "")

        def refined_replace(text):
            # First, handle ![...]([N]) specifically
            # We want to replace the [N] with the actual URL so the markdown renders
            def sub_img_ref(match):
                alt = match.group(1)
                ref = match.group(2)
                inner_match = body_pattern.match(ref)
                if inner_match:
                    oid = int(inner_match.group(1))
                    if oid in image_url_map:
                        return f"![{alt}]({image_url_map[oid]})"
                return match.group(0)
            
            text = re.sub(r'!\[(.*?)\]\((.*?)\)', sub_img_ref, text)
            
            # Then handle normal [N] replacements
            def sub_norm_ref(match):
                oid = int(match.group(1))
                if oid in unified_id_map:
                    return f"[{unified_id_map[oid]}]"
                if oid in image_url_map:
                    return "" # Remove standalone image citations like [5] if they aren't in ![]()
                return "" # Remove hallucinated or invalid citations like [99] if not found in results
            
            return body_pattern.sub(sub_norm_ref, text)

        final_text = refined_replace(remaining_text)
        parsed["response"] = final_text.strip()
        return parsed

    async def _safe_route_tool(self, tool_call):
        """Wrapper for safe concurrent execution of tool calls."""
        try:
            return await asyncio.wait_for(self._route_tool(tool_call), timeout=30.0)
        except asyncio.TimeoutError:
            return "Error: Tool execution timed out (30s limit)."
        except Exception as e:
            return f"Error: Tool execution failed: {e}"

    async def _route_tool(self, tool_call):
        """Execute tool call and return result."""
        name = tool_call.function.name
        args = json.loads(html.unescape(tool_call.function.arguments))

        if name == "internal_web_search" or name == "web_search": 
            query = args.get("query")
            try:
                web = await self.search_service.search(query)
            except Exception as e:
                logger.error(f"Failed to execute search: {e}")
                self._search_error = str(e)
                raise e
            
            # Filter blocked domains immediately
            blocked_domains = getattr(self.config, "fetch_blocked_domains", ["wikipedia.org", "csdn.net", "baidu.com"])
            if isinstance(blocked_domains, str):
                blocked_domains = [d.strip() for d in blocked_domains.split(",")]
            
            # Use list comprehension for filtering
            original_count = len(web)
            web = [
                item for item in web 
                if not any(blocked in item.get("url", "").lower() for blocked in blocked_domains)
            ]
            if len(web) < original_count:
                logger.info(f"Filtered {original_count - len(web)} blocked search results.")
            
            # Cache results and assign global IDs
            for item in web:
                self.global_id_counter += 1
                item["_id"] = self.global_id_counter
                item["_type"] = "search"
                item["query"] = query
                self.all_web_results.append(item)
            
            return json.dumps({"web_results_count": len(web), "status": "cached_for_prompt"}, ensure_ascii=False)

        if name == "internal_image_search":
            query = args.get("query")
            # Start image search in background (non-blocking)
            # Images are for UI rendering only, not passed to LLM
            async def _background_image_search():
                try:
                    images = await self.search_service.image_search(query)
                    # Cache results and assign global IDs for UI rendering
                    for item in images:
                        self.global_id_counter += 1
                        item["_id"] = self.global_id_counter
                        item["_type"] = "image"
                        item["query"] = query
                        item["is_image"] = True
                        self.all_web_results.append(item)
                    logger.info(f"Background image search completed: {len(images)} images for query '{query}'")
                except (asyncio.CancelledError, Exception) as e:
                    # Silently handle cancellation or minor errors in background pre-warming
                    if isinstance(e, asyncio.CancelledError):
                        logger.debug(f"Background image search cancelled for query '{query}'")
                    else:
                        logger.error(f"Background image search failed for query '{query}': {e}")
            
            task = asyncio.create_task(_background_image_search())
            self._image_search_tasks.append(task)
            
            # Return immediately without waiting for search to complete
            return json.dumps({"image_results_count": 0, "status": "searching_in_background"}, ensure_ascii=False)

        if name == "crawl_page":
            url = args.get("url")
            logger.info(f"[Tool] Crawling page: {url}")
            # Returns Dict: {content, title, url}
            result_dict = await self.search_service.fetch_page(url)
            
            # Cache the crawled content with global ID
            self.global_id_counter += 1
            
            # Generate screenshot for direct URL crawl (so LLM can see it)
            screenshot_b64 = await self._render_page_screenshot(
                title=result_dict.get("title", "Page"),
                url=url,
                content=result_dict.get("content", "")[:4000]
            )
            
            cached_item = {
                "_id": self.global_id_counter,
                "_type": "page",
                "title": result_dict.get("title", "Page"),
                "url": result_dict.get("url", url),
                "content": result_dict.get("content", ""),
                "domain": "",
                "is_crawled": True,
                "screenshot_b64": screenshot_b64,  # Add screenshot
            }
            try:
                from urllib.parse import urlparse
                cached_item["domain"] = urlparse(url).netloc
            except:
                pass
            
            self.all_web_results.append(cached_item)
            
            return json.dumps({"crawl_status": "success", "title": cached_item["title"], "content_length": len(result_dict.get("content", ""))}, ensure_ascii=False)

        if name == "set_mode":
            mode = args.get("mode", "standard")
            self.current_mode = mode
            return f"Mode set to {mode}"

        if name == "refuse_answer":
            reason = args.get("reason", "")
            self._should_refuse = True
            self._refuse_reason = reason
            logger.info(f"[Tool] refuse_answer called. Reason: {reason}")
            return "Refuse answer triggered. Pipeline will terminate early."

        return f"Unknown tool {name}"


    async def _safe_llm_call(self, messages, model, tools=None, tool_choice=None, client: Optional[AsyncOpenAI] = None, extra_body: Optional[Dict[str, Any]] = None):
        try:
            return await asyncio.wait_for(
                self._do_llm_request(messages, model, tools, tool_choice, client=client or self.client, extra_body=extra_body),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error("LLM Call Timed Out")
            return type("obj", (object,), {"content": "Error: The model took too long to respond.", "tool_calls": None})(), {"input_tokens": 0, "output_tokens": 0}
        except Exception as e:
            logger.error(f"LLM Call Failed: {e}")
            return type("obj", (object,), {"content": f"Error: Model failure ({e})", "tool_calls": None})(), {"input_tokens": 0, "output_tokens": 0}

    async def _do_llm_request(self, messages, model, tools, tool_choice, client: AsyncOpenAI, extra_body: Optional[Dict[str, Any]] = None):
        try:
            payload_debug = json.dumps(messages)
            logger.info(f"LLM Request Payload Size: {len(payload_debug)} chars")
        except Exception:
            pass

        t0 = time.time()
        logger.info("LLM Request SENT to API...")
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=self.config.temperature,
            extra_body=extra_body,
        )
        logger.info(f"LLM Request RECEIVED after {time.time() - t0:.2f}s")
        
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] = getattr(response.usage, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(response.usage, "completion_tokens", 0) or 0
        
        return response.choices[0].message, usage



    async def _run_instruct_stage(
        self, user_input: str, images: List[str] = None, model: str = None
    ) -> Tuple[str, List[str], Dict[str, Any], Dict[str, int], float]:
        """Returns (instruct_text, search_payloads, trace_dict, usage_dict, search_time).
        
        Images are now passed directly here (merged vision stage).
        """
        # Instruct has access to: web_search, crawl_page, refuse_answer
        tools = [self.web_search_tool, self.crawl_page_tool, self.refuse_answer_tool]
        tools_desc = "- internal_web_search: 搜索文本\n- crawl_page: 获取网页内容\n- refuse_answer: 拒绝回答（敏感/违规内容）"

        prompt = INSTRUCT_SP.format(user_msgs=user_input or "", tools_desc=tools_desc)

        client = self._client_for(
            api_key=getattr(self.config, "instruct_api_key", None),
            base_url=getattr(self.config, "instruct_base_url", None),
        )

        # Build user content - multimodal if images provided
        if images:
            user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_input or "..."}]
            for img_b64 in images:
                url = f"data:image/png;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_input or "..."

        history: List[Dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]

        response, usage = await self._safe_llm_call(
            messages=history,
            model=model,
            tools=tools,
            tool_choice="auto",
            client=client,
            extra_body=getattr(self.config, "instruct_extra_body", None),
        )

        search_payloads: List[str] = []
        instruct_trace: Dict[str, Any] = {
            "model": model,
            "base_url": getattr(self.config, "instruct_base_url", None) or self.config.base_url,
            "prompt": prompt,
            "user_input": user_input or "",
            "has_images": bool(images),
            "images_count": len(images) if images else 0,
            "tool_calls": [],
            "tool_results": [],
            "output": "",
        }
        
        search_time = 0.0
        
        if response.tool_calls:
            plan_dict = response.model_dump() if hasattr(response, "model_dump") else response
            history.append(plan_dict)

            tasks = [self._safe_route_tool(tc) for tc in response.tool_calls]
            
            st = time.time()
            results = await asyncio.gather(*tasks)
            search_time = time.time() - st
            
            for i, result in enumerate(results):
                tc = response.tool_calls[i]
                history.append(
                    {"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": str(result)}
                )
                instruct_trace["tool_calls"].append(self._tool_call_to_trace(tc))
                instruct_trace["tool_results"].append({"name": tc.function.name, "content": str(result)})
                
                if tc.function.name in ["web_search", "internal_web_search"]:
                    search_payloads.append(str(result))

            instruct_trace["output"] = ""
            instruct_trace["usage"] = usage
            return "", search_payloads, instruct_trace, usage, search_time

        instruct_trace["output"] = (response.content or "").strip()
        instruct_trace["usage"] = usage
        return "", search_payloads, instruct_trace, usage, 0.0

    async def _run_auto_fetch_with_screenshots(self, urls: List[str]):
        """
        Automatically fetch URLs and generate screenshots of their content.
        Stops after getting the first 5 successful results (fastest wins).
        Screenshots are stored as base64 in the cached items.
        """
        if not urls:
            return

        # Get config
        fetch_timeout = float(getattr(self.config, "fetch_timeout", 15.0))
        max_results = int(getattr(self.config, "fetch_max_results", 5))

        async def _fetch_and_screenshot(url: str):
            try:
                # Fetch page content
                result_dict = await self.search_service.fetch_page(url)
                
                self.global_id_counter += 1
                
                # Generate screenshot from page content
                screenshot_b64 = await self._render_page_screenshot(
                    title=result_dict.get("title", "Page"),
                    url=url,
                    content=result_dict.get("content", "")[:4000]  # Limit content for screenshot
                )
                
                cached_item = {
                    "_id": self.global_id_counter,
                    "_type": "page",
                    "title": result_dict.get("title", "Page"),
                    "url": result_dict.get("url", url),
                    "content": result_dict.get("content", ""),
                    "images": result_dict.get("images", []),
                    "domain": "",
                    "is_crawled": True,
                    "screenshot_b64": screenshot_b64,
                }
                try:
                    from urllib.parse import urlparse
                    cached_item["domain"] = urlparse(url).netloc
                except:
                    pass
                
                return cached_item
            except Exception as e:
                logger.error(f"Failed to fetch/screenshot {url}: {e}")
                return None

        async def _fetch_with_timeout(url: str):
            """Wrapper to apply timeout to each fetch operation."""
            try:
                return await asyncio.wait_for(_fetch_and_screenshot(url), timeout=fetch_timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Fetch timeout ({fetch_timeout}s) exceeded for: {url}")
                return None

        # Create tasks for all URLs (track url -> task mapping)
        url_to_task = {url: asyncio.create_task(_fetch_with_timeout(url)) for url in urls}
        tasks = list(url_to_task.values())
        first_url = urls[0] if urls else None
        first_task = url_to_task.get(first_url) if first_url else None
        
        # Collect first N successful results (fastest wins)
        collected_results = {}  # url -> result
        successful_count = 0
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                if result:
                    # Find which URL this result belongs to
                    result_url = result.get("url", "")
                    collected_results[result_url] = result
                    successful_count += 1
                    # Only break if we have enough AND first URL is done (or failed)
                    first_done = first_url in collected_results or (first_task and first_task.done())
                    if successful_count >= max_results and first_done:
                        logger.info(f"Got {max_results} successful results, cancelling remaining tasks")
                        break
            except Exception as e:
                logger.warning(f"Fetch task failed: {e}")
        
        # Ensure first URL task completes (if not already) before cancelling others
        if first_task and not first_task.done():
            logger.info("Waiting for first URL to complete...")
            try:
                result = await first_task
                if result:
                    collected_results[result.get("url", first_url)] = result
            except Exception as e:
                logger.warning(f"First URL fetch failed: {e}")
        
        # Cancel remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait briefly for cancellation to propagate
        if any(not t.done() for t in tasks):
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Add results in original URL order (not fetch speed order)
        for url in urls:
            if url in collected_results:
                self.all_web_results.append(collected_results[url])

    async def _render_page_screenshot(self, title: str, url: str, content: str) -> Optional[str]:
        """
        Render page content as a simple HTML and take a screenshot.
        Returns base64 encoded image or None on failure.
        Images are compressed to reduce LLM payload size.
        """
        import base64
        import tempfile
        
        try:
            # Try to use the content renderer if available
            from .render_vue import ContentRenderer
            
            # Create a simple markdown representation for screenshot
            markdown = f"> 来源: {url}\n\n# {title}\n\n{content}"  # Limit content
            
            # Use temp file for screenshot
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            
            # Get or create renderer (reuse if possible)
            if not hasattr(self, '_screenshot_renderer'):
                self._screenshot_renderer = ContentRenderer(auto_start=True)
                await self._screenshot_renderer.start(timeout=10000)
            
            # Await the async render method
            await self._screenshot_renderer.render(
                markdown,
                tmp_path,
                stats={"total_time": 0},
                references=[{"title": title, "url": url, "domain": ""}],
            )
            
            # Compress image to reduce LLM payload size (~350KB target)
            img_bytes = await self._compress_image(tmp_path, max_width=600, quality=70)
            
            # Cleanup
            import os
            os.unlink(tmp_path)
            
            return base64.b64encode(img_bytes).decode("utf-8")
            
        except Exception as e:
            logger.warning(f"Failed to render page screenshot: {e}")
            return None

    async def _compress_image(self, image_path: str, max_width: int = 400, quality: int = 50) -> bytes:
        """Compress image to reduce size for LLM payload."""
        from io import BytesIO
        
        try:
            from PIL import Image
            
            def _compress():
                with Image.open(image_path) as img:
                    # Calculate new height maintaining aspect ratio
                    if img.width > max_width:
                        ratio = max_width / img.width
                        new_height = int(img.height * ratio)
                        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                    
                    # Convert to RGB if necessary
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    
                    # Save to buffer with compression
                    buffer = BytesIO()
                    img.save(buffer, format='JPEG', quality=quality, optimize=True)
                    return buffer.getvalue()
            
            return await asyncio.to_thread(_compress)
            
        except ImportError:
            # PIL not available, return original
            logger.warning("PIL not available for image compression, using original")
            with open(image_path, 'rb') as f:
                return f.read()

    async def _run_summary_stage(
        self, user_input: str, images: List[str] = None, 
        has_page_screenshots: bool = False, model: str = None
    ) -> Tuple[str, Dict[str, int], Dict[str, Any]]:
        """
        Generate final summary using page screenshots only.
        Returns (content, usage, trace_info).
        """
        
        # Build system prompt
        try:
            language_conf = getattr(self.config, "language", "Simplified Chinese")
            system_prompt = SUMMARY_SP.format(language=language_conf)
        except Exception:
            system_prompt = SUMMARY_SP
        

            
        # Build user content - multimodal if images provided
        if images:
            user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_input or "..."}]
            for img_b64 in images:
                url = f"data:image/jpeg;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_input or "..."
            
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        client = self._client_for(
            api_key=getattr(self.config, "summary_api_key", None),
            base_url=getattr(self.config, "summary_base_url", None)
        )
        
        response, usage = await self._safe_llm_call(
            messages=messages,
            model=model,
            client=client,
            extra_body=getattr(self.config, "summary_extra_body", None)
        )
        
        return (response.content or "").strip(), usage, {"prompt": system_prompt}

    def _format_fetch_msgs(self) -> str:
        """Format crawled page content for Summary prompt."""
        if not self.all_web_results:
            return ""

        lines = []
        for res in self.all_web_results:
            if res.get("_type") != "page": continue
            idx = res.get("_id")
            title = (res.get("title", "") or "").strip()
            url = res.get("url", "")
            content = (res.get("content", "") or "").strip()
            # Truncate content if too long? For now keep it full or rely on model context
            lines.append(f"Title: {title}\nURL: {url}\nContent:\n{content}\n")
        
        return "\n".join(lines)

    def _format_search_msgs(self) -> str:
        """Format search snippets only (not crawled pages)."""
        if not self.all_web_results:
            return ""

        lines = []
        for res in self.all_web_results:
            if res.get("_type") != "search": continue  # Only search results
            idx = res.get("_id")
            title = (res.get("title", "") or "").strip()
            url = res.get("url", "")
            content = (res.get("content", "") or "").strip()
            lines.append(f"[{idx}] Title: {title}\nURL: {url}\nSnippet: {content}\n")
        
        return "\n".join(lines)

    def _format_page_msgs(self) -> str:
        """Format crawled page content (detailed)."""
        if not self.all_web_results:
            return ""

        lines = []
        for res in self.all_web_results:
            if res.get("_type") != "page": continue  # Only page results
            idx = res.get("_id")
            title = (res.get("title", "") or "").strip()
            url = res.get("url", "")
            content = (res.get("content", "") or "").strip()
            lines.append(f"[{idx}] Title: {title}\nURL: {url}\nContent: {content}\n")
        
        return "\n".join(lines)

    def _format_image_search_msgs(self) -> str:
        if not self.all_web_results:
            return ""
        
        lines = []
        for res in self.all_web_results:
            if res.get("_type") != "image": continue  # Only image results
            idx = res.get("_id")
            title = res.get("title", "")
            url = res.get("image", "") or res.get("url", "")
            thumb = res.get("thumbnail", "")
            lines.append(f"[{idx}] Title: {title}\nURL: {url}\nThumbnail: {thumb}\n")
        return "\n".join(lines)

    def _client_for(self, api_key: Optional[str], base_url: Optional[str]) -> AsyncOpenAI:
        if api_key or base_url:
            return AsyncOpenAI(base_url=base_url or self.config.base_url, api_key=api_key or self.config.api_key)
        return self.client

    def _tool_call_to_trace(self, tool_call) -> Dict[str, Any]:
        try:
            args = json.loads(html.unescape(tool_call.function.arguments))
        except Exception:
            args = tool_call.function.arguments
        return {"id": getattr(tool_call, "id", None), "name": tool_call.function.name, "arguments": args}

    def _render_trace_markdown(self, trace: Dict[str, Any]) -> str:
        def fence(label: str, content: str) -> str:
            safe = (content or "").replace("```", "``\\`")
            return f"```{label}\n{safe}\n```"

        parts: List[str] = []
        parts.append("# Pipeline Trace\n")

        if trace.get("instruct"):
            t = trace["instruct"]
            parts.append("## Instruct\n")
            parts.append(f"- model: `{t.get('model')}`")
            parts.append(f"- base_url: `{t.get('base_url')}`")
            parts.append(f"- has_images: `{t.get('has_images', False)}`")
            parts.append(f"- images_count: `{t.get('images_count', 0)}`\n")
            parts.append("### Prompt\n")
            parts.append(fence("text", t.get("prompt", "")))
            if t.get("tool_calls"):
                parts.append("\n### Tool Calls\n")
                parts.append(fence("json", json.dumps(t.get("tool_calls"), ensure_ascii=False, indent=2)))
            if t.get("tool_results"):
                parts.append("\n### Tool Results\n")
                parts.append(fence("json", json.dumps(t.get("tool_results"), ensure_ascii=False, indent=2)))
            parts.append("\n### Output\n")
            parts.append(fence("text", t.get("output", "")))
            parts.append("")

        if trace.get("fetch"):
            f = trace["fetch"]
            parts.append("## Auto-Fetch\n")
            parts.append(f"- urls_fetched: `{f.get('urls_fetched', [])}`")
            parts.append(f"- screenshots_count: `{f.get('screenshots_count', 0)}`\n")
            parts.append("")

        if trace.get("summary"):
            s = trace["summary"]
            parts.append("## Summary\n")
            parts.append(f"- model: `{s.get('model')}`\n")
            parts.append("### System Prompt\n")
            parts.append(fence("text", s.get("system_prompt", "")))
            parts.append("\n### Output\n")
            parts.append(fence("text", s.get("output", "")))
            parts.append("")

        return "\n".join(parts).strip() + "\n"

    async def _cache_run_async(self, cache_data: Dict[str, Any]):
        """
        Async background task to cache run data (trace, screenshots) to a folder.
        Saves to data/conversations/{timestamp}_{query}/
        This runs after the response is sent, so it doesn't block the main pipeline.
        """
        import base64
        from datetime import datetime
        from pathlib import Path
        
        try:
            # Create cache directory: data/conversations/{timestamp}_{query}/
            cache_base = Path(getattr(self.config, "conversations_dir", "data/conversations"))
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            user_input_short = (cache_data.get("user_input", "query") or "query")[:20]
            # Clean filename
            user_input_short = "".join(c if c.isalnum() or c in "._-" else "_" for c in user_input_short)
            cache_dir = cache_base / f"{timestamp}_{user_input_short}"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            # Save conversation markdown (includes trace and response)
            conversation_md = f"""# {cache_data.get("user_input", "Query")}

## Response

{cache_data.get("final_content", "")}

---

## Trace

{cache_data.get("trace_markdown", "")}
"""
            conv_path = cache_dir / "conversation.md"
            await asyncio.to_thread(
                conv_path.write_text,
                conversation_md,
                encoding="utf-8"
            )
            
            # Save page screenshots
            screenshots = cache_data.get("page_screenshots", [])
            for i, screenshot_b64 in enumerate(screenshots):
                if screenshot_b64:
                    screenshot_path = cache_dir / f"page_{i+1}.jpg"
                    img_bytes = base64.b64decode(screenshot_b64)
                    await asyncio.to_thread(screenshot_path.write_bytes, img_bytes)
            
            logger.debug(f"Conversation cached to: {cache_dir}")
            
        except Exception as e:
            # Don't fail silently but also don't crash the pipeline
            logger.warning(f"Failed to cache conversation: {e}")

    async def close(self):
        try:
            await self.search_service.close()
        except Exception:
            pass

        # Gracefully handle background tasks completion
        if hasattr(self, '_image_search_tasks') and self._image_search_tasks:
            for task in self._image_search_tasks:
                if not task.done(): task.cancel()
            try:
                # Wait briefly for cancellation to propagate
                await asyncio.wait(self._image_search_tasks, timeout=0.2)
            except Exception: pass
            self._image_search_tasks = []

        # Also cleanup image cache pending tasks if any
        try:
            from .image_cache import get_image_cache
            cache = get_image_cache()
            if cache._pending:
                pending = list(cache._pending.values())
                for task in pending:
                    if not task.done(): task.cancel()
                await asyncio.wait(pending, timeout=0.2)
                cache._pending.clear()
        except Exception: pass
        
        self.all_web_results = []
