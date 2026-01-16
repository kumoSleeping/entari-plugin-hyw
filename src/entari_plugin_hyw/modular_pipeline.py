"""
Modular Pipeline Dispatcher

New pipeline architecture: Instruct Loop (x2) -> Summary.
Simpler flow with self-correction/feedback loop.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from .stage_base import StageContext
from .stage_instruct import InstructStage
from .stage_instruct_review import InstructReviewStage
from .stage_summary import SummaryStage
from .search import SearchService


class ModularPipeline:
    """
    Modular Pipeline.
    
    Flow:
    1. Instruct (Round 1): Initial Discovery.
    2. Instruct Review (Round 2): Review & Refine.
    3. Summary: Generate final response.
    """
    
    def __init__(self, config: Any):
        self.config = config
        self.search_service = SearchService(config)
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        
        # Initialize stages
        self.instruct_stage = InstructStage(config, self.search_service, self.client)
        self.instruct_review_stage = InstructReviewStage(config, self.search_service, self.client)
        self.summary_stage = SummaryStage(config, self.search_service, self.client)
    
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
        
        context = StageContext(
            user_input=user_input,
            images=images or [],
            conversation_history=conversation_history,
        )
        
        trace: Dict[str, Any] = {
            "instruct_rounds": [],
            "summary": None,
        }
        
        try:
            logger.info(f"Pipeline: Processing '{user_input[:30]}...'")
            
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

            # === Stage 2: Instruct Review (Refine) ===
            logger.info("Pipeline: Stage 2 - Instruct Review")
            review_result = await self.instruct_review_stage.execute(context)
            
            # Trace & Usage
            review_result.trace["stage_name"] = "Instruct Review (Round 2)"
            trace["instruct_rounds"].append(review_result.trace)
            usage_totals["input_tokens"] += review_result.usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += review_result.usage.get("output_tokens", 0)
            
            # === Stage 3: Summary ===
            # Collect page screenshots if image mode (already rendered in InstructStage)
            all_images = list(images) if images else []
            
            if getattr(self.config, "page_content_mode", "text") == "image":
                # Collect pre-rendered screenshots from web_results
                for r in context.web_results:
                    if r.get("_type") == "page" and r.get("screenshot_b64"):
                        all_images.append(r["screenshot_b64"])
            
            summary_result = await self.summary_stage.execute(
                context,
                images=all_images if all_images else None
            )
            trace["summary"] = summary_result.trace
            usage_totals["input_tokens"] += summary_result.usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += summary_result.usage.get("output_tokens", 0)
            
            summary_content = summary_result.data.get("content", "")
            
            # === Result Assembly ===
            stats["total_time"] = time.time() - start_time
            structured = self._parse_response(summary_content, context)
            
            # === Image Caching (Prefetch images for UI) ===
            try:
                from .image_cache import get_image_cache
                cache = get_image_cache()
                
                # 1. Collect all image URLs from structured response
                all_image_urls = []
                for ref in structured.get("references", []):
                    if ref.get("images"):
                        all_image_urls.extend([img for img in ref["images"] if img and img.startswith("http")])
                
                if all_image_urls:
                    # 2. Prefetch (wait for them as we are about to render)
                    cached_map = await cache.get_all_cached(all_image_urls)
                    
                    # 3. Update structured response with cached (base64) URLs
                    for ref in structured.get("references", []):
                        if ref.get("images"):
                            # Filter: Only keep images that were successfully cached (starts with data:)
                            # Discard original URLs if download failed, to prevent broken images in UI
                            new_images = []
                            for img in ref["images"]:
                                cached_val = cached_map.get(img)
                                if cached_val and cached_val.startswith("data:"):
                                    new_images.append(cached_val)
                            ref["images"] = new_images
            except Exception as e:
                logger.warning(f"Pipeline: Image caching failed: {e}")
            
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
