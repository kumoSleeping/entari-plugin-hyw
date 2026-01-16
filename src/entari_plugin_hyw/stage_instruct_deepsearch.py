"""
Instruct Deepsearch Stage

Handles the deepsearch loop: Supplement information until sufficient or max iterations reached.
Inherits from InstructStage to reuse tool execution logic.
"""

import time
from typing import Any, List
from loguru import logger
from openai import AsyncOpenAI

from .stage_base import StageContext, StageResult
from .stage_instruct import InstructStage
from .definitions import INSTRUCT_DEEPSEARCH_SP

class InstructDeepsearchStage(InstructStage):
    @property
    def name(self) -> str:
        return "Instruct Deepsearch"
    
    def __init__(self, config: Any, search_service: Any, client: AsyncOpenAI):
        super().__init__(config, search_service, client)
        # Inherits tools from InstructStage (web_search, crawl_page)
    
    async def execute(self, context: StageContext) -> StageResult:
        start_time = time.time()
        logger.info("Instruct Deepsearch: Starting supplementary research")
        
        # Check if we have context to review
        if not context.review_context:
            logger.warning("Instruct Deepsearch: No context found. Skipping.")
            return StageResult(
                success=True, 
                data={"reasoning": "Skipped due to missing context.", "should_stop": True}
            )
            
        # Build System Prompt (Clean)
        system_prompt = INSTRUCT_DEEPSEARCH_SP
        
        # Build Messages
        # Inject context as a separate user message explaining the background
        context_message = f"## 已收集的信息\n\n```context\n{context.review_context}\n```"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_message},
            {"role": "user", "content": self._build_user_message(context)}
        ]
        
        # Call LLM
        # We use only web_search and crawl_page tools (no set_mode, no refuse_answer in this stage)
        tools = [self.web_search_tool, self.crawl_page_tool]
        
        response, usage, tool_calls, content = await self._call_llm(
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        # Check for empty response = signal to stop
        should_stop = False
        if not tool_calls or len(tool_calls) == 0:
            logger.info("Instruct Deepsearch: No tool calls, signaling to stop loop.")
            should_stop = True
        else:
            # Execute Tools
            tool_outputs = await self._process_tool_calls(context, tool_calls)
            
            # Update context for next iteration
            iteration_summary = f"\n## Deepsearch Iteration\n"
            if content:
                iteration_summary += f"Thought: {content}\n"
            for output in tool_outputs:
                iteration_summary += f"- {output['name']}: {output['content'][:200]}...\n"
            context.review_context += iteration_summary
            
            # Update history
            context.instruct_history.append({
                "role": "assistant",
                "content": f"[Deepsearch]: {content}\n[Actions]: {len(tool_outputs)} tools"
            })
        
        return self._build_result(start_time, usage, content, len(tool_calls or []), should_stop)

    def _build_result(self, start_time, usage, content, tool_calls_count, should_stop=False):
        model_cfg = self.config.get_model_config("instruct")
        model = model_cfg.get("model_name") or self.config.model_name

        trace = {
            "stage": "Instruct Deepsearch",
            "model": model, 
            "usage": usage,
            "output": content,
            "tool_calls": tool_calls_count,
            "time": time.time() - start_time,
        }
        
        return StageResult(
            success=True,
            data={"reasoning": content, "should_stop": should_stop},
            usage=usage,
            trace=trace
        )
