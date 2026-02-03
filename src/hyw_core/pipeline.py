"""
Modular Pipeline Dispatcher

New pipeline architecture: Instruct Loop (x2) -> Summary.
Simpler flow with self-correction/feedback loop.
"""

import asyncio
import time
import re
from typing import Any, Dict, List, Optional, Callable, Awaitable

from loguru import logger
from openai import AsyncOpenAI

from .stages.base import StageContext, StageResult
from .stages.base import StageContext, StageResult, BaseStage
from .stages.summary import SummaryStage
from .tools.duckduckgo_search import DuckDuckGoSearchService as SearchService


class ModularPipeline:
    """
    Modular Pipeline.
    
    Flow:
    1. Input Analysis:
       - If Images -> Skip Search -> Summary
       - If Text -> Execute Search (or URL fetch) -> Summary
    2. Summary: Generate final response.
    """
    
    def __init__(self, config: Any, search_service: SearchService, send_func: Optional[Callable[[str], Awaitable[None]]] = None):
        self.config = config
        self.send_func = send_func
        self.search_service = search_service
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        
        # Initialize stages
        self.summary_stage = SummaryStage(config, self.search_service, self.client)
    
    @property
    def _send_func(self) -> Optional[Callable[[str], Awaitable[None]]]:
        """Getter for _send_func (alias for send_func)."""
        return self.send_func
    
    @_send_func.setter
    def _send_func(self, value: Optional[Callable[[str], Awaitable[None]]]):
        """Setter for _send_func - updates send_func and propagates to stages."""
        self.send_func = value
    
    
    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict],
        model_name: str = None,
        images: List[str] = None,
    ) -> Dict[str, Any]:
        """Execute the modular pipeline."""
        start_time = time.time()
        stats = {"start_time": start_time}
        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        active_model = model_name or self.config.model_name
        if not active_model:
             # Fallback to instruct model for logging/context
             active_model = self.config.get_model_config("instruct").model_name
        
        context = StageContext(
            user_input=user_input,
            images=images or [],
            conversation_history=conversation_history,
        )
        
        # Determine if model supports image input
        model_cfg_dict = next((m for m in self.config.models if m.get("name") == active_model), None)
        if model_cfg_dict:
            context.image_input_supported = model_cfg_dict.get("image_input", True)
        else:
             context.image_input_supported = True # Default to True if unknown
             
        logger.info(f"Pipeline Execution: Model '{active_model}' Image Input Supported: {context.image_input_supported}")

        
        trace: Dict[str, Any] = {
            "instruct_rounds": [],
            "summary": None,
        }
        
        try:
            logger.info(f"Pipeline: Processing '{user_input[:30]}...'")
            
            # === Image-First Logic ===
            # When user provides images, skip search and go directly to Instruct
            # Images will be passed through to both Instruct and Summary stages
            has_user_images = bool(images)
            if has_user_images:
                logger.info(f"Pipeline: {len(images)} user image(s) detected. Skipping search -> Instruct.")
            
            # === Search-First Logic (only when no images) ===
            # 1. URL Detection
            # Updated to capture full URLs including queries and paths
            url_pattern = re.compile(r'https?://(?:[-\w./?=&%#]+)')
            found_urls = url_pattern.findall(user_input)
            
            hit_content = False
            
            # Skip URL fetch and search if user provided images or long query
            is_long_query = len(user_input) > 20
            if has_user_images:
                hit_content = False  # Force into Instruct path
            elif is_long_query:
                logger.info(f"Pipeline: Long query ({len(user_input)} chars). Skipping direct search/fetch -> Instruct.")
                hit_content = False
            elif found_urls:
                logger.info(f"Pipeline: Detected {len(found_urls)} URLs. Executing direct fetch...")
                # Fetch pages (borrowing logic from InstructStage's batch fetch would be ideal, 
                # but we'll use search_service directly and simulate what Instruct did for context)
                
                # Fetch
                fetch_results = await self.search_service.fetch_pages_batch(found_urls)
                
                # Pre-render screenshots if needed (similar to InstructStage logic)
                # For brevity/cleanliness, assuming fetch_pages_batch returns what we need or we process it.
                # Ideally we want screenshots for the UI. The serivce.fetch_page usually returns raw data.
                # We need to render them if we want screenshots.
                # To keep it simple for this file, we'll skip complex screenshot rendering here OR 
                # we rely on the summary stage to just use the text. 
                # But the user logic implies "Search/Fetch Hit -> Summary".
                
                # Let's populate context.web_results
                for i, page_data in enumerate(fetch_results):
                    if page_data.get("content"):
                         hit_content = True
                         context.web_results.append({
                             "_id": context.next_id(),
                             "_type": "page",
                             "title": page_data.get("title", "Page"),
                             "url": page_data.get("url", found_urls[i]),
                             "content": page_data.get("content", ""),
                             "images": page_data.get("images", []),
                             # For now, no screenshot unless we call renderer. 
                             # If critical, we can add it later.
                         })

            # 2. Search (if no URLs or just always try search if simple query?)
            # The prompt says: "judging result quantity > 0".
            if not hit_content and not has_user_images and not is_long_query and user_input.strip():
                logger.info("Pipeline: No URLs found or fetched. Executing direct search...")
                search_start = time.time()
                search_results = await self.search_service.search(user_input)
                context.search_time = time.time() - search_start
                
                # Filter out the raw debug page
                valid_results = [r for r in search_results if not r.get("_hidden")]
                
                if valid_results:
                    logger.info(f"Pipeline: Search found {len(valid_results)} results in {context.search_time:.2f}s. Proceeding to Summary.")
                    hit_content = True
                    for item in search_results: # Add all, including hidden debug ones if needed by history
                        item["_id"] = context.next_id()
                        if "_type" not in item: item["_type"] = "search"
                        item["query"] = user_input
                        context.web_results.append(item)
                else:
                    logger.info("Pipeline: Search yielded 0 results.")

            # === Branching ===
            if hit_content and not has_user_images:
                # -> Summary Stage (search/URL results available)
                logger.info("Pipeline: Content found (URL/Search). Proceeding to Summary.")
            
            # If no content was found and no images, we still proceed to Summary but with empty context (Direct Chat)
            # If images, we proceed to Summary with images.
            
            # Refusal check from search results? (Unlikely, but good to keep in mind)
            pass
            
            
            # === Parallel Execution: Summary Generation + Image Prefetching ===
            # We run image prefetching concurrently with Summary generation to save time.
            
            # 1. Prepare candidates for prefetch (all images in search results)
            all_candidate_urls = set()
            for r in context.web_results:
                # Add images from search results/pages
                if r.get("images"):
                    for img in r["images"]:
                        if img and isinstance(img, str) and img.startswith("http"):
                            all_candidate_urls.add(img)
            
            prefetch_list = list(all_candidate_urls)
            logger.info(f"Pipeline: Starting parallel execution (Summary + Prefetch {len(prefetch_list)} images)")
            
            # 2. Define parallel tasks with timing
            async def timed_summary():
                t0 = time.time()
                # Collect page screenshots if image mode
                summary_input_images = list(images) if images else []
                if context.image_input_supported:
                    # Collect pre-rendered screenshots from web_results
                    for r in context.web_results:
                        if r.get("_type") == "page" and r.get("screenshot_b64"):
                            summary_input_images.append(r["screenshot_b64"])
                
                if context.should_refuse:
                     return StageResult(success=True, data={"content": "Refused"}, usage={}, trace={}), 0.0

                res = await self.summary_stage.execute(
                    context,
                    images=summary_input_images if summary_input_images else None
                )
                duration = time.time() - t0
                return res, duration

            async def timed_prefetch():
                t0 = time.time()
                if not prefetch_list:
                    return {}, 0.0
                try:
                    from .image_cache import get_image_cache
                    cache = get_image_cache()
                    # Start prefetch (non-blocking kickoff)
                    cache.start_prefetch(prefetch_list)
                    # Wait for results (blocking until done)
                    res = await cache.get_all_cached(prefetch_list)
                    duration = time.time() - t0
                    return res, duration
                except Exception as e:
                    logger.warning(f"Pipeline: Prefetch failed: {e}")
                    return {}, time.time() - t0

            # 3. Execute concurrently
            summary_task = asyncio.create_task(timed_summary())
            prefetch_task = asyncio.create_task(timed_prefetch())
            
            # Wait for both to complete
            await asyncio.wait([summary_task, prefetch_task])
            
            # 4. Process results and log timing
            summary_result, summary_time = await summary_task
            cached_map, prefetch_time = await prefetch_task
            
            if context.should_refuse:
                # Double check if summary triggered refusal
                return self._build_refusal_response(context, conversation_history, active_model, stats)

            time_diff = abs(summary_time - prefetch_time)
            if summary_time > prefetch_time:
                logger.info(f"Pipeline: Image Prefetch finished first ({prefetch_time:.2f}s). Summary took {summary_time:.2f}s. (Waited {time_diff:.2f}s for Summary)")
            else:
                logger.info(f"Pipeline: Summary finished first ({summary_time:.2f}s). Image Prefetch took {prefetch_time:.2f}s. (Waited {time_diff:.2f}s for Prefetch)")
            
            trace["summary"] = summary_result.trace
            usage_totals["input_tokens"] += summary_result.usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += summary_result.usage.get("output_tokens", 0)
            
            summary_content = summary_result.data.get("content", "")
            
            # === Result Assembly ===
            stats["total_time"] = time.time() - start_time
            structured = self._parse_response(summary_content, context)
            
            # === Apply Cached Images ===
            # Update structured response using the map from parallel prefetch
            if cached_map:
                try:
                    total_replaced = 0
                    for ref in structured.get("references", []):
                        if ref.get("images"):
                            new_images = []
                            for img in ref["images"]:
                                # 1. Already Base64 -> Keep it
                                if img.startswith("data:"):
                                    new_images.append(img)
                                    continue
                                
                                # 2. Check cache
                                cached_val = cached_map.get(img)
                                if cached_val and cached_val.startswith("data:"):
                                    new_images.append(cached_val)
                                    total_replaced += 1
                                # 3. Else -> DROP IT (as per policy)
                            ref["images"] = new_images
                    logger.debug(f"Pipeline: Replaced {total_replaced} images with cached versions")
                except Exception as e:
                    logger.warning(f"Pipeline: Applying cached images failed: {e}")
            
            # Debug: Log image counts
            total_ref_images = sum(len(ref.get("images", []) or []) for ref in structured.get("references", []))
            logger.info(f"Pipeline: Final structured response has {len(structured.get('references', []))} refs with {total_ref_images} images total")
            
            stages_used = self._build_stages_ui(trace, context, images)
            
            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({"role": "assistant", "content": summary_content})
            
            return {
                "llm_response": summary_content,
                "structured_response": structured,
                "stats": stats,
                "model_used": active_model,
                "conversation_history": conversation_history,
                "trace_markdown": self._render_trace_markdown(trace),
                "billing_info": {
                    "input_tokens": usage_totals["input_tokens"],
                    "output_tokens": usage_totals["output_tokens"],
                    "total_cost": 0.0
                },
                "stages_used": stages_used,
                "web_results": context.web_results,
                "trace": trace,

                "instruct_traces": trace.get("instruct_rounds", []),
            }

        except Exception as e:
            logger.error(f"Pipeline: Critical Error - {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "llm_response": f"Error: {e}",
                "stats": stats,
                "error": str(e)
            }

    def _build_refusal_response(self, context, history, model, stats):
        return {
            "llm_response": "Refused",
            "structured_response": {},
            "stats": stats,
            "model_used": model,
            "conversation_history": history,
            "refuse_answer": True,
            "refuse_reason": context.refuse_reason
        }

    def _parse_response(self, text: str, context: StageContext) -> Dict[str, Any]:
        """Parse response and extract citations, prioritizing fetched items."""
        import re
        parsed = {"response": "", "references": [], "page_references": [], "image_references": []}
        if not text: return parsed
        
        # Simple cleanup
        ref_pattern = re.compile(r'(?:\n\s*|^)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources|参考资料)[\s\S]*$', re.IGNORECASE | re.MULTILINE)
        body_text = ref_pattern.sub('', text)
        
        # 1. Identify all cited numeric IDs from [N]
        cited_ids = []
        for m in re.finditer(r'\[(\d+)\]', body_text):
            try:
                cid = int(m.group(1))
                if cid not in cited_ids: cited_ids.append(cid)
            except: pass
            
        # 2. Collect cited items and determine "is_fetched" status
        cited_items = []
        for cid in cited_ids:
            item = next((r for r in context.web_results if r.get("_id") == cid), None)
            if not item: continue
            
            # Check if this URL was fetched (appears as a "page" result)
            is_fetched = any(r.get("_type") == "page" and r.get("url") == item.get("url") for r in context.web_results)
            cited_items.append({
                "original_id": cid,
                "item": item,
                "is_fetched": is_fetched
            })
            
        # 3. Sort: Fetched pages first, then regular search results
        cited_items.sort(key=lambda x: x["is_fetched"], reverse=True)
        
        # 4. Create Re-indexing Map
        reindex_map = {}
        for i, entry in enumerate(cited_items):
            reindex_map[entry["original_id"]] = i + 1
            
            # Populate result references in sorted order
            item = entry["item"]
            ref_entry = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "domain": item.get("domain", ""),
                "snippet": (item.get("content", "") or "")[:200] + "...", # More snippet
                "is_fetched": entry["is_fetched"],
                "type": item.get("_type", "search"),
                "raw_screenshot_b64": item.get("raw_screenshot_b64"),  # Real page screenshot for Sources
                "images": item.get("images"),
            }
            # Add to unified list (frontend can handle splitting if needed, but we provide sorted order)
            parsed["references"].append(ref_entry)
            
        # 5. Replace [N] in text with new indices
        def repl(m):
            try:
                oid = int(m.group(1))
                return f"[{reindex_map[oid]}]" if oid in reindex_map else m.group(0)
            except: return m.group(0)
            
        parsed["response"] = re.sub(r'\[(\d+)\]', repl, body_text).strip()
        return parsed

    def _build_stages_ui(self, trace: Dict[str, Any], context: StageContext, images: List[str]) -> List[Dict[str, Any]]:
        stages = []
        
        # 1. Search Results
        search_refs = []
        seen = set()
        for r in context.web_results:
            if r.get("_type") == "search" and r.get("url") not in seen:
                seen.add(r["url"])
                is_fetched = any(p.get("url") == r["url"] for p in context.web_results if p.get("_type") == "page")
                search_refs.append({
                    "title": r.get("title", ""),
                    "url": r["url"],
                    "snippet": (r.get("content", "") or "")[:100] + "...",
                    "is_fetched": is_fetched
                })
        
        # Sort: Fetched first
        search_refs.sort(key=lambda x: x["is_fetched"], reverse=True)
        
        logger.debug(f"_build_stages_ui: Found {len(search_refs)} search refs from {len(context.web_results)} web_results")
        
        if search_refs:
            stages.append({
                "name": "Search",
                "model": "Web Search",
                "icon_config": "openai",
                "provider": "Web",
                "references": search_refs,
                "description": f"Found {len(search_refs)} results.",
                "time": getattr(context, 'search_time', 0)
            })
        
        # 2. Instruct Rounds
        for i, t in enumerate(trace.get("instruct_rounds", [])):
            stage_name = t.get("stage_name", f"Analysis {i+1}")
            tool_count = t.get("tool_calls", 0)
            desc = t.get("output", "")
            
            if tool_count > 0:
                # If tools were used, prefer showing tool info even if there's reasoning
                desc = f"Executed {tool_count} tool calls."
            elif not desc:
                desc = "Processing..."
            
            # Calculate cost from config prices
            usage = t.get("usage", {})
            instruct_cfg = self.config.get_model_config("instruct")
            input_price = instruct_cfg.input_price or 0
            output_price = instruct_cfg.output_price or 0
            cost = (usage.get("input_tokens", 0) * input_price + usage.get("output_tokens", 0) * output_price) / 1_000_000
            
            stages.append({
                "name": stage_name,
                "model": t.get("model"),
                "icon_config": "google",
                "provider": "Instruct",
                "time": t.get("time", 0),
                "description": desc,
                "usage": usage,
                "cost": cost
            })
            
        # 3. Summary
        if trace.get("summary"):
            s = trace["summary"]
            usage = s.get("usage", {})
            main_cfg = self.config.get_model_config("main")
            input_price = main_cfg.input_price or 0
            output_price = main_cfg.output_price or 0
            cost = (usage.get("input_tokens", 0) * input_price + usage.get("output_tokens", 0) * output_price) / 1_000_000
            
            stages.append({
                "name": "Summary",
                "model": s.get("model"),
                "icon_config": "google",
                "provider": "Summary",
                "time": s.get("time", 0),
                "description": "Generated final answer.",
                "usage": usage,
                "cost": cost
            })
            
        return stages

    def _render_trace_markdown(self, trace: Dict[str, Any]) -> str:
        parts = ["# Pipeline Trace\n"]
        if trace.get("instruct_rounds"):
            parts.append(f"## Instruct ({len(trace['instruct_rounds'])} rounds)\n")
            for i, r in enumerate(trace["instruct_rounds"]):
                 name = r.get("stage_name", f"Round {i+1}")
                 parts.append(f"### {name}\n" + str(r))
        if trace.get("summary"):
            parts.append("## Summary\n" + str(trace["summary"]))
        return "\n".join(parts)
    
    async def close(self):
        try:
            await self.search_service.close()
        except: pass
