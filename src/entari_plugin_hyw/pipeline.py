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
    AGENT_SP,
    AGENT_SP_INSTRUCT_VISION_ADD,
    AGENT_SP_TOOLS_STANDARD_ADD,
    AGENT_SP_TOOLS_AGENT_ADD,
    AGENT_SP_SEARCH_ADD,
    INSTRUCT_SP,
    INSTRUCT_SP_VISION_ADD,
    VISION_SP,
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
        self.image_search_tool = {
            "type": "function",
            "function": {
                "name": "internal_image_search",
                "description": "Search for images related to a query.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
        self.set_mode_tool = {
            "type": "function",
            "function": {
                "name": "set_mode",
                "description": "设定后续 Agent 的运行模式: standard | agent",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["standard", "agent"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["mode"],
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
                        "reason": {"type": "string", "description": "拒绝回答的原因（内部记录，不展示给用户）"},
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
        1) Vision: summarize images once (no image persistence).
        2) Instruct: run web_search and decide whether to grant Playwright MCP tools.
        3) Agent: normally no tools; if granted, allow Playwright MCP tools (max 6 rounds; step 5 nudge, step 6 forced).
        """
        start_time = time.time()
        stats = {"start_time": start_time, "tool_calls_count": 0}
        # Token usage tracking for billing
        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        active_model = model_name or self.config.model_name

        current_history = conversation_history
        final_response_content = ""
        structured: Dict[str, Any] = {}
        
        # Reset search cache and ID counter for this execution
        self.all_web_results = []
        self.global_id_counter = 0
        # Reset refuse_answer flag
        self._should_refuse = False
        self._refuse_reason = ""

        try:
            logger.info(f"Pipeline: Starting workflow for '{user_input}' using {active_model}")

            trace: Dict[str, Any] = {
                "vision": None,
                "instruct": None,
                "agent": None,
            }

            # Vision stage
            vision_text = ""
            vision_start = time.time()
            vision_time = 0
            vision_cost = 0.0
            vision_usage = {}
            if images:
                vision_model = (
                    selected_vision_model
                    or vision_model_name
                    or getattr(self.config, "vision_model_name", None)
                    or active_model
                )
                vision_prompt = VISION_SP.format(user_msgs=user_input or "[图片]")
                vision_text, vision_usage = await self._run_vision_stage(
                    user_input=user_input,
                    images=images,
                    model=vision_model,
                    prompt=vision_prompt,
                )
                # Add vision usage with vision-specific pricing
                usage_totals["input_tokens"] += vision_usage.get("input_tokens", 0)
                usage_totals["output_tokens"] += vision_usage.get("output_tokens", 0)
                
                # Calculate Vision Cost
                v_in_price = float(getattr(self.config, "vision_input_price", None) or getattr(self.config, "input_price", 0.0) or 0.0)
                v_out_price = float(getattr(self.config, "vision_output_price", None) or getattr(self.config, "output_price", 0.0) or 0.0)
                if v_in_price > 0 or v_out_price > 0:
                     vision_cost = (vision_usage.get("input_tokens", 0) / 1_000_000 * v_in_price) + (vision_usage.get("output_tokens", 0) / 1_000_000 * v_out_price)

                vision_time = time.time() - vision_start
                
                trace["vision"] = {
                    "model": vision_model,
                    "base_url": getattr(self.config, "vision_base_url", None) or self.config.base_url,
                    "prompt": vision_prompt,
                    "user_input": user_input or "",
                    "images_count": len(images or []),
                    "output": vision_text,
                    "usage": vision_usage,
                    "time": vision_time,
                    "cost": vision_cost
                }

            # Instruct + pre-search
            instruct_start = time.time()
            instruct_model = getattr(self.config, "instruct_model_name", None) or active_model
            logger.info(f"Instruct Stage Config: instruct_model_name={getattr(self.config, 'instruct_model_name', None)}, active_model={active_model}, using: {instruct_model}")
            instruct_text, search_payloads, instruct_trace, instruct_usage, search_time = await self._run_instruct_stage(
                user_input=user_input,
                vision_text=vision_text,
                model=instruct_model,
            )
            # Instruct time excludes search time (search_time is returned separately)
            instruct_time = time.time() - instruct_start - search_time
            
            # Calculate Instruct Cost
            instruct_cost = 0.0
            i_in_price = float(getattr(self.config, "instruct_input_price", None) or getattr(self.config, "input_price", 0.0) or 0.0)
            i_out_price = float(getattr(self.config, "instruct_output_price", None) or getattr(self.config, "output_price", 0.0) or 0.0)
            if i_in_price > 0 or i_out_price > 0:
                instruct_cost = (instruct_usage.get("input_tokens", 0) / 1_000_000 * i_in_price) + (instruct_usage.get("output_tokens", 0) / 1_000_000 * i_out_price)
            
            # Add instruct usage
            usage_totals["input_tokens"] += instruct_usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += instruct_usage.get("output_tokens", 0)
            
            instruct_trace["time"] = instruct_time
            instruct_trace["cost"] = instruct_cost
            trace["instruct"] = instruct_trace

            # Check if refuse_answer was called - terminate early
            if self._should_refuse:
                logger.info(f"Pipeline: refuse_answer triggered. Reason: {self._refuse_reason}")
                stats["total_time"] = time.time() - start_time
                return {
                    "llm_response": "",
                    "structured_response": {},
                    "stats": stats,
                    "model_used": active_model,
                    "conversation_history": current_history,
                    "refuse_answer": True,
                    "refuse_reason": self._refuse_reason,
                    "stages_used": [],
                }

            # Start agent loop
            agent_start_time = time.time()
            current_history.append({"role": "user", "content": user_input or "..."})

            mode = instruct_trace.get("mode", self.current_mode).lower()
            logger.success(f"Instruct Mode: {mode}")
            self.current_mode = mode
            
            # Determine max iterations
            max_steps = 10 if mode == "agent" else 1 
            
            step = 0
            agent_trace_steps: List[Dict[str, Any]] = []
            last_system_prompt = ""

            agent_tools: Optional[List[Dict[str, Any]]] = None
            if mode == "agent":
                agent_tools = [self.web_search_tool, self.image_search_tool, self.crawl_page_tool]

            # Agent loop
            while step < max_steps:
                step += 1
                logger.info(f"Pipeline: Agent step {step}/{max_steps}")

                if step == 5 and mode == "agent":
                    current_history.append(
                        {
                            "role": "system",
                            "content": "System: [Next Step Final] Please start consolidating the answer; the next step must be the final response.",
                        }
                    )

                tools_desc = ""
                if agent_tools:
                    tools_desc = "\n".join([
                        "- internal_web_search(query): 触发搜索并缓存结果",
                        "- crawl_page(url): 使用 Crawl4AI 抓取网页返回 Markdown"
                    ])

                user_msgs_text = user_input or ""

                search_msgs_text = self._format_search_msgs()
                # Image search results are NOT passed to LLM - they're for UI rendering only
                
                has_search_results = any(r.get("_type") == "search" for r in self.all_web_results)
                has_image_results = any(r.get("_type") == "image" for r in self.all_web_results)  # For UI rendering only

                # Build agent system prompt
                mode_desc_text = AGENT_SP_TOOLS_AGENT_ADD.format(tools_desc=tools_desc) if mode == "agent" else AGENT_SP_TOOLS_STANDARD_ADD
                system_prompt = AGENT_SP.format(
                    user_msgs=user_msgs_text,
                    mode=mode,
                    mode_desc=mode_desc_text,
                    language=getattr(self.config, "language", "Simplified Chinese")[:128]
                )
                
                # Append vision text if available
                if vision_text:
                    system_prompt += AGENT_SP_INSTRUCT_VISION_ADD.format(vision_msgs=vision_text)
                
                # Append search results (text and page only, NOT images)
                page_msgs_text = self._format_page_msgs()
                all_search_parts = []
                if has_search_results and search_msgs_text:
                    all_search_parts.append(search_msgs_text)
                if page_msgs_text:
                    all_search_parts.append(page_msgs_text)
                # Images are excluded from LLM prompt - they're for UI rendering only
                
                if all_search_parts:
                    system_prompt += AGENT_SP_SEARCH_ADD.format(search_msgs="\n".join(all_search_parts))

                last_system_prompt = system_prompt

                messages = [{"role": "system", "content": system_prompt}]
                messages.extend(current_history)

                tools_for_step = agent_tools if (agent_tools and step < max_steps) else None
                
                # Debug logging
                if tools_for_step:
                    logger.info(f"[Agent] Tools provided: {[t['function']['name'] for t in tools_for_step]}")
                else:
                    logger.warning(f"[Agent] NO TOOLS provided for step {step} (agent_tools={agent_tools is not None}, step<max={step < max_steps})")
                
                step_llm_start = time.time()
                response, step_usage = await self._safe_llm_call(
                    messages=messages,
                    model=active_model,
                    tools=tools_for_step,
                    tool_choice="auto" if tools_for_step else None,
                    extra_body=self.config.extra_body,
                )
                step_llm_time = time.time() - step_llm_start
                
                # Debug: Check response
                has_tool_calls = response.tool_calls is not None and len(response.tool_calls) > 0
                logger.info(f"[Agent] Response has_tool_calls={has_tool_calls}, has_content={bool(response.content)}")
                
                # Accumulate agent usage
                usage_totals["input_tokens"] += step_usage.get("input_tokens", 0)
                usage_totals["output_tokens"] += step_usage.get("output_tokens", 0)

                if response.tool_calls and tools_for_step:
                    tool_calls = response.tool_calls
                    stats["tool_calls_count"] += len(tool_calls)

                    # Use model_dump to preserve provider-specific fields (e.g., Gemini's thought_signature)
                    assistant_msg = response.model_dump(exclude_unset=True) if hasattr(response, "model_dump") else {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in tool_calls]
                    }
                    current_history.append(assistant_msg)

                    tasks = [self._safe_route_tool(tc) for tc in tool_calls]
                    tool_start_time = time.time()
                    results = await asyncio.gather(*tasks)
                    tool_exec_time = time.time() - tool_start_time

                    step_trace = {
                        "step": step,
                        "tool_calls": [self._tool_call_to_trace(tc) for tc in tool_calls],
                        "tool_results": [],
                        "tool_time": tool_exec_time,
                        "llm_time": step_llm_time,
                        "usage": step_usage,
                    }
                    for i, result in enumerate(results):
                        tc = tool_calls[i]
                        step_trace["tool_results"].append({"name": tc.function.name, "content": str(result)})
                        current_history.append(
                            {
                                "tool_call_id": tc.id,
                                "role": "tool",
                                "name": tc.function.name,
                                "content": str(result),
                            }
                        )
                    agent_trace_steps.append(step_trace)
                    continue

                final_response_content = response.content or ""
                current_history.append({"role": "assistant", "content": final_response_content})
                agent_trace_steps.append({
                    "step": step, 
                    "final": True, 
                    "output": final_response_content,
                    "llm_time": step_llm_time,
                    "usage": step_usage
                })
                break

            if not final_response_content:
                final_response_content = "执行结束，但未生成内容。"

            structured = self._parse_tagged_response(final_response_content)
            final_content = structured.get("response") or final_response_content

            agent_time = time.time() - agent_start_time
            
            # Calculate Agent Cost
            agent_cost = 0.0
            a_in_price = float(getattr(self.config, "input_price", 0.0) or 0.0)
            a_out_price = float(getattr(self.config, "output_price", 0.0) or 0.0)
            
            agent_input_tokens = usage_totals["input_tokens"] - vision_usage.get("input_tokens", 0) - instruct_usage.get("input_tokens", 0)
            agent_output_tokens = usage_totals["output_tokens"] - vision_usage.get("output_tokens", 0) - instruct_usage.get("output_tokens", 0)
            
            if a_in_price > 0 or a_out_price > 0:
                agent_cost = (max(0, agent_input_tokens) / 1_000_000 * a_in_price) + (max(0, agent_output_tokens) / 1_000_000 * a_out_price)

            trace["agent"] = {
                "model": active_model,
                "base_url": self.config.base_url,
                "system_prompt": last_system_prompt,
                "steps": agent_trace_steps,
                "final_output": final_response_content,
                "time": agent_time,
                "cost": agent_cost
            }
            trace_markdown = self._render_trace_markdown(trace)

            stats["total_time"] = time.time() - start_time
            stats["steps"] = step

            # Calculate billing info correctly by summing up all actual costs
            total_cost_sum = vision_cost + instruct_cost
            for s in agent_trace_steps:
                s_usage = s.get("usage", {})
                if s_usage:
                    s_in_price = float(getattr(self.config, "input_price", 0.0) or 0.0)
                    s_out_price = float(getattr(self.config, "output_price", 0.0) or 0.0)
                    total_cost_sum += (s_usage.get("input_tokens", 0) / 1_000_000 * s_in_price) + (s_usage.get("output_tokens", 0) / 1_000_000 * s_out_price)

            billing_info = {
                "input_tokens": usage_totals["input_tokens"],
                "output_tokens": usage_totals["output_tokens"],
                "total_cost": total_cost_sum,
            }

            # Build stages_used list for UI display
            stages_used = []
            
            def infer_icon(model_name: str, base_url: str) -> str:
                model_lower = (model_name or "").lower()
                url_lower = (base_url or "").lower()
                if "deepseek" in model_lower or "deepseek" in url_lower: return "deepseek"
                elif "claude" in model_lower or "anthropic" in url_lower: return "anthropic"
                elif "gemini" in model_lower or "google" in url_lower: return "google"
                elif "gpt" in model_lower or "openai" in url_lower: return "openai"
                elif "qwen" in model_lower: return "qwen"
                elif "openrouter" in url_lower: return "openrouter"
                return "openai" 
            
            def infer_provider(base_url: str) -> str:
                url_lower = (base_url or "").lower()
                if "openrouter" in url_lower: return "OpenRouter"
                elif "openai" in url_lower: return "OpenAI"
                elif "anthropic" in url_lower: return "Anthropic"
                elif "google" in url_lower: return "Google"
                elif "deepseek" in url_lower: return "DeepSeek"
                return ""
            
            if trace.get("vision"):
                v = trace["vision"]
                v_model = v.get("model", "")
                v_base_url = v.get("base_url", "") or self.config.base_url
                stages_used.append({
                    "name": "Vision",
                    "model": v_model,
                    "icon_config": infer_icon(v_model, v_base_url),
                    "provider": infer_provider(v_base_url),
                    "time": v.get("time", 0),
                    "cost": v.get("cost", 0.0)
                })
            
            if trace.get("instruct"):
                i = trace["instruct"]
                i_model = i.get("model", "")
                i_base_url = i.get("base_url", "") or self.config.base_url
                stages_used.append({
                    "name": "Instruct",
                    "model": i_model,
                    "icon_config": infer_icon(i_model, i_base_url),
                    "provider": infer_provider(i_base_url),
                    "time": i.get("time", 0),
                    "cost": i.get("cost", 0.0)
                })

            # Show Search stage if we have ANY search results (text OR image)
            if (has_search_results or has_image_results) and search_payloads:
                # Collect initial search results for the Search stage card
                initial_refs = [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "domain": r.get("domain", "")}
                    for r in self.all_web_results if r.get("_type") == "search"
                ]
                initial_images = [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "thumbnail": r.get("thumbnail", "")}
                    for r in self.all_web_results if r.get("_type") == "image"
                ]
                
                stages_used.append({
                    "name": "Search",
                    "model": getattr(self.config, "search_name", "DuckDuckGo"),
                    "icon_config": "search",
                    "provider": getattr(self.config, 'search_provider', 'Crawl4AI'),
                    "time": search_time,
                    "cost": 0.0,
                    "references": initial_refs,
                    "image_references": initial_images
                })
            
            # Add Crawler stage if Instruct used crawl_page
            if trace.get("instruct"):
                instruct_tool_calls = trace["instruct"].get("tool_calls", [])
                crawl_calls = [tc for tc in instruct_tool_calls if tc.get("name") == "crawl_page"]
                if crawl_calls:
                    # Build crawled_pages list for UI
                    crawled_pages = []
                    for tc in crawl_calls:
                        url = tc.get("arguments", {}).get("url", "")
                        # Try to find cached result
                        found = next((r for r in self.all_web_results if r.get("url") == url and r.get("_type") == "page"), None)
                        if found:
                            try:
                                from urllib.parse import urlparse
                                domain = urlparse(url).netloc
                            except:
                                domain = ""
                            crawled_pages.append({
                                "title": found.get("title", "Page"),
                                "url": url,
                                "favicon_url": f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
                            })
                    
                    stages_used.append({
                        "name": "Crawler",
                        "model": "Crawl4AI",
                        "icon_config": "search",
                        "provider": "网页抓取",
                        "time": search_time,  # Use existing search_time which includes fetch time
                        "cost": 0.0,
                        "crawled_pages": crawled_pages
                    })
            
            # --- Granular Agent Stages (Grouped) ---
            if trace.get("agent"):
                a = trace["agent"]
                a_model = a.get("model", "") or active_model
                a_base_url = a.get("base_url", "") or self.config.base_url
                steps = a.get("steps", [])
                agent_icon = infer_icon(a_model, a_base_url)
                agent_provider = infer_provider(a_base_url)

                for s in steps:
                    if "tool_calls" in s:
                        # 1. Agent Thought Stage (with LLM time)
                        # Calculate step cost
                        step_usage = s.get("usage", {})
                        step_cost = 0.0
                        if a_in_price > 0 or a_out_price > 0:
                             step_cost = (step_usage.get("input_tokens", 0) / 1_000_000 * a_in_price) + (step_usage.get("output_tokens", 0) / 1_000_000 * a_out_price)

                        stages_used.append({
                            "name": "Agent",
                            "model": a_model,
                            "icon_config": agent_icon,
                            "provider": agent_provider,
                            "time": s.get("llm_time", 0), "cost": step_cost
                        })
                        
                        # 2. Grouped Tool Stages
                        # Collect results for grouping
                        search_group_items = []
                        crawler_group_items = []
                        
                        tcs = s.get("tool_calls", [])
                        trs = s.get("tool_results", [])
                        
                        for idx, tc in enumerate(tcs):
                            t_name = tc.get("name")
                            # Try to get result content if available
                            t_res_content = trs[idx].get("content", "") if idx < len(trs) else ""

                            if t_name in ["internal_web_search", "web_search", "internal_image_search"]:
                                # We don't have per-call metadata easily unless we parse the 'result' string (which is JSON dump now for route_tool)
                                # But search results are cached in self.all_web_results.
                                # The 'content' of search tool result is basically "cached_for_prompt". 
                                # So we don't need to put items here, just show "Search" container. 
                                # But wait, if we want to show "what was searched", we can parse args.
                                args = tc.get("arguments", {})
                                query = args.get("query", "")
                                if query: 
                                    search_group_items.append({"query": query})

                            elif t_name == "crawl_page":
                                # Get URL from arguments, title from result
                                args = tc.get("arguments", {})
                                url = args.get("url", "")
                                title = "Page"
                                try:
                                    page_data = json.loads(t_res_content)
                                    if isinstance(page_data, dict):
                                        title = page_data.get("title", "Page")
                                except:
                                    pass
                                
                                if url:
                                    try:
                                        domain = urlparse(url).netloc
                                    except:
                                        domain = ""
                                    crawler_group_items.append({
                                        "title": title,
                                        "url": url,
                                        "favicon_url": f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
                                    })

                        # Append Grouped Stages
                        if search_group_items:
                             stages_used.append({
                                "name": "Search",
                                "model": getattr(self.config, "search_name", "DuckDuckGo"),
                                "icon_config": "search",
                                "provider": "Agent Search",
                                "time": s.get("tool_time", 0), "cost": 0,
                                "queries": search_group_items # Render can use this if needed, or just show generic
                            })
                        
                        if crawler_group_items:
                            stages_used.append({
                                "name": "Crawler",
                                "model": "Crawl4AI",
                                "icon_config": "browser", 
                                "provider": "Page Fetcher",
                                "time": s.get("tool_time", 0), "cost": 0,
                                "crawled_pages": crawler_group_items 
                            })

                    elif s.get("final"):
                        # Correctly calculate final step cost
                        step_usage = s.get("usage", {})
                        step_cost = 0.0
                        if a_in_price > 0 or a_out_price > 0:
                             step_cost = (step_usage.get("input_tokens", 0) / 1_000_000 * a_in_price) + (step_usage.get("output_tokens", 0) / 1_000_000 * a_out_price)

                        stages_used.append({
                            "name": "Agent",
                            "model": a_model,
                            "icon_config": agent_icon,
                            "provider": agent_provider,
                            "time": s.get("llm_time", 0), 
                            "cost": step_cost
                        })

            # Assign total time/cost to last Agent stage
            # Sum up total time/cost for UI/stats (implicit via loop above)
            # No need to assign everything to last agent anymore as we distribute it.

            # --- Final Filter: Only show cited items in workflow cards ---
            cited_urls = {ref['url'] for ref in (structured.get("references", []) + 
                                               structured.get("page_references", []) + 
                                               structured.get("image_references", []))}
            
            # Find images already rendered in markdown content (to avoid duplicate display)
            markdown_image_urls = set()
            md_img_pattern = re.compile(r'!\[.*?\]\((https?://[^)]+)\)')
            for match in md_img_pattern.finditer(final_content):
                markdown_image_urls.add(match.group(1))
            
            for s in stages_used:
                if "references" in s and s["references"]:
                    s["references"] = [r for r in s["references"] if r.get("url") in cited_urls]
                # Filter out images already shown in markdown content
                # Check both url AND thumbnail since either might be used in markdown
                if "image_references" in s and s["image_references"]:
                    s["image_references"] = [
                        r for r in s["image_references"] 
                        if r.get("url") not in markdown_image_urls and (r.get("thumbnail") or "") not in markdown_image_urls
                    ]
                if "crawled_pages" in s and s["crawled_pages"]:
                    s["crawled_pages"] = [r for r in s["crawled_pages"] if r.get("url") in cited_urls]

            # Clean up conversation history: Remove tool calls and results to save tokens and avoid ID conflicts
            # Keep only 'user' messages and 'assistant' messages without tool_calls (final answers)
            cleaned_history = []
            for msg in current_history:
                if msg.get("role") == "tool":
                    continue
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    continue
                cleaned_history.append(msg)
            
            # Update the reference (since it might be used by caller)
            current_history[:] = cleaned_history

            # --- Apply cached images to reduce render time ---
            # Collect all image URLs that need caching (avoid duplicates when thumbnail == url)
            all_image_urls = set()
            for img_ref in structured.get("image_references", []):
                if img_ref.get("thumbnail"):
                    all_image_urls.add(img_ref["thumbnail"])
                if img_ref.get("url"):
                    all_image_urls.add(img_ref["url"])
            
            for stage in stages_used:
                for img_ref in stage.get("image_references", []):
                    if img_ref.get("thumbnail"):
                        all_image_urls.add(img_ref["thumbnail"])
                    if img_ref.get("url"):
                        all_image_urls.add(img_ref["url"])
            
            # Also collect image URLs from markdown content
            markdown_img_pattern = re.compile(r'!\[.*?\]\((https?://[^)]+)\)')
            markdown_urls = markdown_img_pattern.findall(final_content)
            all_image_urls.update(markdown_urls)
            
            # Get cached versions (waits for pending downloads until agent ends)
            if all_image_urls:
                try:
                    cached_map = await get_cached_images(list(all_image_urls))
                    
                    # Apply cached URLs to structured response
                    for img_ref in structured.get("image_references", []):
                        if img_ref.get("thumbnail") and img_ref["thumbnail"] in cached_map:
                            img_ref["thumbnail"] = cached_map[img_ref["thumbnail"]]
                        if img_ref.get("url") and img_ref["url"] in cached_map:
                            img_ref["url"] = cached_map[img_ref["url"]]
                    
                    # Apply cached URLs to stages
                    for stage in stages_used:
                        for img_ref in stage.get("image_references", []):
                            if img_ref.get("thumbnail") and img_ref["thumbnail"] in cached_map:
                                img_ref["thumbnail"] = cached_map[img_ref["thumbnail"]]
                            if img_ref.get("url") and img_ref["url"] in cached_map:
                                img_ref["url"] = cached_map[img_ref["url"]]
                    
                    # Replace image URLs in markdown content with cached versions
                    def replace_markdown_img(match):
                        full_match = match.group(0)
                        url = match.group(1)
                        cached_url = cached_map.get(url)
                        if cached_url and cached_url != url:
                            return full_match.replace(url, cached_url)
                        return full_match
                    
                    final_content = markdown_img_pattern.sub(replace_markdown_img, final_content)
                    structured["response"] = markdown_img_pattern.sub(replace_markdown_img, structured.get("response", ""))
                    
                    # Log cache stats
                    from .image_cache import get_image_cache
                    cache_stats = get_image_cache().get_stats()
                    logger.info(f"ImageCache stats: {cache_stats}")
                    
                except Exception as e:
                    logger.warning(f"Failed to apply image cache: {e}")

            # Cancel all background image search/download tasks when agent ends
            if self._image_search_tasks:
                logger.info(f"Cancelling {len(self._image_search_tasks)} background image search tasks")
                for task in self._image_search_tasks:
                    if not task.done():
                        task.cancel()
                # Wait a bit for tasks to handle cancellation gracefully
                try:
                    await asyncio.gather(*self._image_search_tasks, return_exceptions=True)
                except Exception:
                    pass
                self._image_search_tasks.clear()
            
            # Also cancel any pending image downloads in the cache
            from .image_cache import get_image_cache
            cache = get_image_cache()
            if cache._pending:
                logger.info(f"Cancelling {len(cache._pending)} pending image downloads")
                for task in cache._pending.values():
                    if not task.done():
                        task.cancel()
                cache._pending.clear()

            return {
                "llm_response": final_content,
                "structured_response": structured,
                "stats": stats,
                "model_used": active_model,
                "vision_model_used": (selected_vision_model or getattr(self.config, "vision_model_name", None)) if images else None,
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
                # Wait briefly for cleanup
                await asyncio.wait(self._image_search_tasks, timeout=0.1)
                self._image_search_tasks.clear()

            from .image_cache import get_image_cache
            cache = get_image_cache()
            if cache._pending:
                pending_tasks = list(cache._pending.values())
                for task in pending_tasks:
                    if not task.done(): task.cancel()
                await asyncio.wait(pending_tasks, timeout=0.1)
                cache._pending.clear()
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
            web = await self.search_service.search(query)
            
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
            
            cached_item = {
                "_id": self.global_id_counter,
                "_type": "page",
                "title": result_dict.get("title", "Page"),
                "url": result_dict.get("url", url),
                "content": result_dict.get("content", ""),
                "domain": "",
                "is_crawled": True,
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

    async def _run_vision_stage(self, user_input: str, images: List[str], model: str, prompt: str) -> Tuple[str, Dict[str, int]]:
        content_payload: List[Dict[str, Any]] = [{"type": "text", "text": user_input or ""}]
        for img_b64 in images:
            url = f"data:image/png;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
            content_payload.append({"type": "image_url", "image_url": {"url": url}})

        client = self._client_for(
            api_key=getattr(self.config, "vision_api_key", None),
            base_url=getattr(self.config, "vision_base_url", None),
        )
        response, usage = await self._safe_llm_call(
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content_payload}],
            model=model,
            client=client,
            extra_body=getattr(self.config, "vision_extra_body", None),
        )
        return (response.content or "").strip(), usage

    async def _run_instruct_stage(
        self, user_input: str, vision_text: str, model: str
    ) -> Tuple[str, List[str], Dict[str, Any], Dict[str, int], float]:
        """Returns (instruct_text, search_payloads, trace_dict, usage_dict, search_time)."""
        # Instruct has access to: web_search, image_search, set_mode, crawl_page, refuse_answer
        tools = [self.web_search_tool, self.image_search_tool, self.set_mode_tool, self.crawl_page_tool, self.refuse_answer_tool]
        tools_desc = "- internal_web_search: 搜索文本\n- internal_image_search: 搜索图片\n- crawl_page: 获取网页内容\n- set_mode: 设定standard/agent模式\n- refuse_answer: 拒绝回答（敏感/违规内容）"

        prompt = INSTRUCT_SP.format(user_msgs=user_input or "", tools_desc=tools_desc)
        
        if vision_text:
            prompt = f"{prompt}\\n\\n{INSTRUCT_SP_VISION_ADD.format(vision_msgs=vision_text)}"

        client = self._client_for(
            api_key=getattr(self.config, "instruct_api_key", None),
            base_url=getattr(self.config, "instruct_base_url", None),
        )

        history: List[Dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input or "..."},
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
            "vision_add": vision_text or "",
            "tool_calls": [],
            "tool_results": [],
            "output": "",
        }
        
        search_time = 0.0
        mode = "standard"
        mode_reason = ""
        
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
                elif tc.function.name == "set_mode":
                    try:
                        args = json.loads(html.unescape(tc.function.arguments))
                    except Exception:
                        args = {}
                    mode = args.get("mode", mode)
                    mode_reason = args.get("reason", "")

            instruct_trace["mode"] = mode
            if mode_reason:
                instruct_trace["mode_reason"] = mode_reason
            
            instruct_trace["output"] = ""
            instruct_trace["usage"] = usage
            return "", search_payloads, instruct_trace, usage, search_time

        instruct_trace["mode"] = mode
        instruct_trace["output"] = (response.content or "").strip()
        instruct_trace["usage"] = usage
        return "", search_payloads, instruct_trace, usage, 0.0

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

        if trace.get("vision"):
            v = trace["vision"]
            parts.append("## Vision\n")
            parts.append(f"- model: `{v.get('model')}`")
            parts.append(f"- base_url: `{v.get('base_url')}`")
            parts.append(f"- images_count: `{v.get('images_count')}`\n")
            parts.append("### Prompt\n")
            parts.append(fence("text", v.get("prompt", "")))
            parts.append("\n### Output\n")
            parts.append(fence("text", v.get("output", "")))
            parts.append("")

        if trace.get("instruct"):
            t = trace["instruct"]
            parts.append("## Instruct\n")
            parts.append(f"- model: `{t.get('model')}`")
            parts.append(f"- base_url: `{t.get('base_url')}`\n")
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

        if trace.get("agent"):
            a = trace["agent"]
            parts.append("## Agent\n")
            parts.append(f"- model: `{a.get('model')}`")
            parts.append(f"- base_url: `{a.get('base_url')}`\n")
            parts.append("### System Prompt\n")
            parts.append(fence("text", a.get("system_prompt", "")))
            parts.append("\n### Steps\n")
            parts.append(fence("json", json.dumps(a.get("steps", []), ensure_ascii=False, indent=2)))
            parts.append("\n### Final Output\n")
            parts.append(fence("text", a.get("final_output", "")))

        return "\n".join(parts).strip() + "\n"

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
