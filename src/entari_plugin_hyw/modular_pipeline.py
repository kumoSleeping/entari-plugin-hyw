"""
Modular Pipeline Dispatcher

New pipeline architecture: Instruct Loop (x2) -> Summary.
Simpler flow with self-correction/feedback loop.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Callable, Awaitable

from loguru import logger
from openai import AsyncOpenAI

from .stage_base import StageContext
from .stage_instruct import InstructStage
from .stage_instruct_deepsearch import InstructDeepsearchStage
from .stage_summary import SummaryStage
from .stage_vision import VisionStage
from .search import SearchService


class ModularPipeline:
    """
    Modular Pipeline.
    
    Flow:
    1. Instruct: Initial Discovery + Mode Decision (fast/deepsearch).
    2. [Deepsearch only] Instruct Deepsearch Loop: Supplement info (max 3 iterations).
    3. Summary: Generate final response.
    """
    
    def __init__(self, config: Any, send_func: Optional[Callable[[str], Awaitable[None]]] = None):
        self.config = config
        self.send_func = send_func
        self.search_service = SearchService(config)
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        
        # Initialize stages
        self.instruct_stage = InstructStage(config, self.search_service, self.client, send_func=send_func)
        self.instruct_deepsearch_stage = InstructDeepsearchStage(config, self.search_service, self.client)
        self.summary_stage = SummaryStage(config, self.search_service, self.client)
        self.vision_stage = VisionStage(config, self.search_service, self.client)
    
    def _has_vision_model(self) -> bool:
        """Check if a vision model is configured."""
        vision_cfg = self.config.get_model_config("vision")
        return bool(vision_cfg.get("model_name"))
    
    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict],
        model_name: str = None,
        images: List[str] = None,
        vision_model_name: str = None,
        selected_vision_model: str = None,
    ) -> Dict[str, Any]:
        """Execute the modular pipeline."""
        start_time = time.time()
        stats = {"start_time": start_time}
        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        active_model = model_name or self.config.model_name
        if not active_model:
             # Fallback to instruct model for logging/context
             active_model = self.config.get_model_config("instruct").get("model_name")
        
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
            
            # === Stage 0: Vision (if images and vision model configured) ===
            if images and self._has_vision_model():
                logger.info("Pipeline: Stage 0 - Vision (generating image description)")
                vision_result = await self.vision_stage.execute(context, images)
                
                if vision_result.success and vision_result.data.get("description"):
                    context.vision_description = vision_result.data["description"]
                    logger.info(f"Pipeline: Vision description generated ({len(context.vision_description)} chars)")
                    
                    # Add vision trace
                    trace["vision"] = vision_result.trace
                    usage_totals["input_tokens"] += vision_result.usage.get("input_tokens", 0)
                    usage_totals["output_tokens"] += vision_result.usage.get("output_tokens", 0)
                    
                    # Clear images since we have the description now
                    # (don't pass raw images to later stages when using vision model)
                    images = []
            
            # === Stage 1: Instruct (Initial Discovery) ===
            logger.info("Pipeline: Stage 1 - Instruct")
            instruct_result = await self.instruct_stage.execute(context)
            
            # Trace & Usage
            instruct_result.trace["stage_name"] = "Instruct (Round 1)"
            trace["instruct_rounds"].append(instruct_result.trace)
            usage_totals["input_tokens"] += instruct_result.usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += instruct_result.usage.get("output_tokens", 0)
            
            # Check refuse
            if context.should_refuse:
                return self._build_refusal_response(context, conversation_history, active_model, stats)

            # === Stage 2: Deepsearch Loop (if mode is deepsearch) ===
            if context.selected_mode == "deepsearch":
                MAX_DEEPSEARCH_ITERATIONS = 3
                logger.info(f"Pipeline: Mode is 'deepsearch', starting loop (max {MAX_DEEPSEARCH_ITERATIONS} iterations)")
                
                for i in range(MAX_DEEPSEARCH_ITERATIONS):
                    logger.info(f"Pipeline: Stage 2 - Deepsearch Iteration {i + 1}")
                    deepsearch_result = await self.instruct_deepsearch_stage.execute(context)
                    
                    # Trace & Usage
                    deepsearch_result.trace["stage_name"] = f"Deepsearch (Iteration {i + 1})"
                    trace["instruct_rounds"].append(deepsearch_result.trace)
                    usage_totals["input_tokens"] += deepsearch_result.usage.get("input_tokens", 0)
                    usage_totals["output_tokens"] += deepsearch_result.usage.get("output_tokens", 0)
                    
                    # Check if should stop
                    if deepsearch_result.data.get("should_stop"):
                        logger.info(f"Pipeline: Deepsearch loop ended at iteration {i + 1}")
                        break
            else:
                logger.info("Pipeline: Mode is 'fast', skipping deepsearch stage")
            
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
                "vision_trace": trace.get("vision"),
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
        
        if search_refs:
            stages.append({
                "name": "Search",
                "model": "Web Search",
                "icon_config": "openai",
                "provider": "Web",
                "references": search_refs,
                "description": f"Found {len(search_refs)} results."
            })
        
        # 2. Vision Stage (if used)
        if trace.get("vision"):
            v = trace["vision"]
            if not v.get("skipped"):
                usage = v.get("usage", {})
                vision_cfg = self.config.get_model_config("vision")
                input_price = vision_cfg.get("input_price") or 0
                output_price = vision_cfg.get("output_price") or 0
                cost = (usage.get("input_tokens", 0) * input_price + usage.get("output_tokens", 0) * output_price) / 1_000_000
                
                stages.append({
                    "name": "Vision",
                    "model": v.get("model"),
                    "icon_config": "google",
                    "provider": "Vision",
                    "time": v.get("time", 0),
                    "description": f"Analyzed {v.get('images_count', 0)} image(s).",
                    "usage": usage,
                    "cost": cost
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
            input_price = instruct_cfg.get("input_price") or 0
            output_price = instruct_cfg.get("output_price") or 0
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
            input_price = main_cfg.get("input_price") or 0
            output_price = main_cfg.get("output_price") or 0
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
