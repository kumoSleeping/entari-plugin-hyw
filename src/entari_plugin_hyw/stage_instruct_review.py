"""
Instruct Review Stage

Handles the second round of instruction: Review and Refine.
Inherits from InstructStage to reuse tool execution logic.
"""

import time
from typing import Any, List
from loguru import logger
from openai import AsyncOpenAI

from .stage_base import StageContext, StageResult
from .stage_instruct import InstructStage
from .definitions import INSTRUCT_REVIEW_SP

class InstructReviewStage(InstructStage):
    @property
    def name(self) -> str:
        return "Instruct Review"
    
    def __init__(self, config: Any, search_service: Any, client: AsyncOpenAI):
        super().__init__(config, search_service, client)
        # Inherits tools from InstructStage
    
    async def execute(self, context: StageContext) -> StageResult:
        start_time = time.time()
        logger.info("Instruct Review: Starting Round 2 (Review & Refine)")
        
        # Check if we have context to review
        if not context.review_context:
            logger.warning("Instruct Review: No context found from Round 1. Skipping.")
            return StageResult(success=True, data={"reasoning": "Skipped due to missing context."})
            
        # Build System Prompt (Clean)
        system_prompt = INSTRUCT_REVIEW_SP
        
        # Build Messages
        # Inject context as a separate user message explaining the background
        context_message = f"## Previous Round Context\n\n```context\n{context.review_context}\n```"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_message},
            {"role": "user", "content": self._build_user_message(context)}
        ]
        
        # Call LLM
        # We reuse _call_llm from parent
        # We reuse tools from parent (refuse_answer might be redundant but harmless, or we can filter)
        tools = [self.web_search_tool, self.crawl_page_tool] # Review prompt doesn't mention refuse_answer explicitly, but usually fine.
        
        response, usage, tool_calls, content = await self._call_llm(
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        # Execute Tools
        tool_outputs = []
        if tool_calls:
            tool_outputs = await self._process_tool_calls(context, tool_calls)
            
        # Update history logic? 
        # The prompt says "上下文". It is "independent". 
        # But for the record, we might want to log it.
        context.instruct_history.append({
             "role": "assistant",
             "content": f"[Round 2 Review]: {content}\n[Round 2 Actions]: {len(tool_outputs)} tools"
        })
        
        return self._build_result(start_time, usage, content, len(tool_calls or []))

    def _build_result(self, start_time, usage, content, tool_calls_count):
        model_cfg = self.config.get_model_config("instruct")
        model = model_cfg.get("model_name") or self.config.model_name

        trace = {
            "stage": "Instruct Review",
            "model": model, 
            "usage": usage,
            "output": content,
            "tool_calls": tool_calls_count,
            "time": time.time() - start_time,
        }
        
        return StageResult(
            success=True,
            data={"reasoning": content},
            usage=usage,
            trace=trace
        )
