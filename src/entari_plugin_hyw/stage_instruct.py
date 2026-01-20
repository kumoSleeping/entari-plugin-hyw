"""
Instruct Stage

Handles initial task planning and search generation.
Analyze user query and execute initial searches.
"""

import json
import time
import asyncio
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from loguru import logger
from openai import AsyncOpenAI

from .stage_base import BaseStage, StageContext, StageResult
from .definitions import (
    get_refuse_answer_tool,
    get_web_search_tool,
    get_crawl_page_tool,
    get_set_mode_tool,
    INSTRUCT_SP
)

class InstructStage(BaseStage):
    @property
    def name(self) -> str:
        return "Instruct"
    
    def __init__(self, config: Any, search_service: Any, client: AsyncOpenAI, send_func: Optional[Callable[[str], Awaitable[None]]] = None):
        super().__init__(config, search_service, client)
        self.send_func = send_func
        
        self.refuse_answer_tool = get_refuse_answer_tool()
        self.web_search_tool = get_web_search_tool()
        self.crawl_page_tool = get_crawl_page_tool()
        self.set_mode_tool = get_set_mode_tool()

    async def execute(self, context: StageContext) -> StageResult:
        start_time = time.time()
        
        # --- Round 1: Initial Discovery ---
        logger.info("Instruct: Starting Round 1 (Initial Discovery)")
        
        # Build Round 1 User Message
        r1_user_content = self._build_user_message(context)
        r1_messages = [
            {"role": "system", "content": INSTRUCT_SP},
            {"role": "user", "content": r1_user_content}
        ]
        
        # Execute Round 1 LLM
        r1_response, r1_usage, r1_tool_calls, r1_content = await self._call_llm(
            messages=r1_messages,
            tools=[self.refuse_answer_tool, self.web_search_tool, self.crawl_page_tool, self.set_mode_tool],
            tool_choice="auto"
        )
        
        if context.should_refuse:
             # If refused in Round 1, stop here
             return self._build_result(start_time, r1_usage, r1_content, len(r1_tool_calls or []))

        # Execute Round 1 Tools
        r1_tool_outputs = []
        if r1_tool_calls:
            r1_tool_outputs = await self._process_tool_calls(context, r1_tool_calls)
        
        # --- Context Assembly for Round 2 ---
        
        # Summarize Round 1 actions for context
        r1_summary_text = "## Round 1 Execution Summary\n"
        if r1_content:
            r1_summary_text += f"Thought: {r1_content}\n"
            
        if r1_tool_outputs:
            r1_summary_text += "Tools Executed & Results:\n"
            for output in r1_tool_outputs:
                # content here is the tool output (e.g. search results text or crawl preview)
                r1_summary_text += f"- Action: {output['name']}\n"
                r1_summary_text += f"  Result: {output['content']}\n"
        else:
            r1_summary_text += "No tools were executed in Round 1.\n"
            
        r2_context_str = f"""User Query: {context.user_input}
        
{r1_summary_text}
"""
        # Save to context for next stage
        context.review_context = r2_context_str
        
        # Update instruct_history for logging/record purposes
        context.instruct_history.append({
            "role": "assistant",
            "content": f"[Round 1 Thought]: {r1_content}\n[Round 1 Actions]: {len(r1_tool_outputs)} tools"
        })
        
        return self._build_result(start_time, r1_usage, r1_content, len(r1_tool_calls or []))

    def _build_user_message(self, context: StageContext) -> Any:
        text_prompt = f"User Query: {context.user_input}"
        if context.images:
            user_content: List[Dict[str, Any]] = [{"type": "text", "text": text_prompt}]
            for img_b64 in context.images:
                url = f"data:image/jpeg;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
                user_content.append({"type": "image_url", "image_url": {"url": url}})
            return user_content
        return text_prompt

    async def _call_llm(self, messages, tools, tool_choice="auto"):
        model_cfg = self.config.get_model_config("instruct")
        client = self._client_for(
            api_key=model_cfg.get("api_key"),
            base_url=model_cfg.get("base_url")
        )
        model = model_cfg.get("model_name") or self.config.model_name
        
        try:
            logger.info(f"Instruct: Sending LLM request to {model}...")
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=self.config.temperature,
                extra_body=model_cfg.get("extra_body"),
            )
        except Exception as e:
            logger.error(f"InstructStage LLM Error: {e}")
            raise e
            
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] = getattr(response.usage, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(response.usage, "completion_tokens", 0) or 0
            
        message = response.choices[0].message
        content = message.content or ""
        tool_calls = message.tool_calls
        
        if content:
             logger.debug(f"Instruct: Agent Thought -> {content[:100]}...")
             
        return response, usage, tool_calls, content

    async def _process_tool_calls(self, context: StageContext, tool_calls: List[Any]) -> List[Dict[str, Any]]:
        """
        Executes tool calls and returns a list of outputs for context building.
        Updates context.web_results globally.
        """
        pending_crawls = []    # List of (url, tool_call_id)
        pending_searches = []  # List of (query, tool_call_id)
        
        results_for_context = []

        for tc in tool_calls:
            name = tc.function.name
            tc_id = tc.id
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                results_for_context.append({
                    "id": tc_id, "name": name, "content": "Error: Invalid JSON arguments"
                })
                continue
            
            if name == "refuse_answer":
                reason = args.get("reason", "Refused")
                logger.warning(f"Instruct: Model Refused Answer. Reason: {reason}")
                context.should_refuse = True
                context.refuse_reason = reason
                results_for_context.append({
                     "id": tc_id, "name": name, "content": f"Refused: {reason}"
                })
            
            elif name == "web_search":
                query = args.get("query")
                if query: 
                    logger.info(f"Instruct: Planned search query -> '{query}'")
                    pending_searches.append((query, tc_id))
            
            elif name == "crawl_page":
                url = args.get("url")
                if url: 
                    logger.info(f"Instruct: Planned page crawl -> {url}")
                    pending_crawls.append((url, tc_id))
            
            elif name == "set_mode":
                mode = args.get("mode", "fast")
                if mode in ("fast", "deepsearch"):
                    context.selected_mode = mode
                    logger.info(f"Instruct: Mode set to '{mode}'")
                    
                    # Notify immediately if deepsearch
                    if mode == "deepsearch" and self.send_func:
                        try:
                            await self.send_func("🔍 正在进行深度研究，可能需要一些时间，请耐心等待...")
                        except Exception as e:
                            logger.warning(f"Instruct: Failed to send notification: {e}")

                    results_for_context.append({
                        "id": tc_id, "name": name, "content": f"Mode set to: {mode}"
                    })
                else:
                    logger.warning(f"Instruct: Invalid mode '{mode}', defaulting to 'fast'")
                    context.selected_mode = "fast"
        
        # Execute Batches
        
        # 1. Crawls
        if pending_crawls:
            urls = [u for u, _ in pending_crawls]
            logger.info(f"Instruct: Executing {len(urls)} crawls via batch...")
            
            # Start fetch
            fetch_task = asyncio.create_task(self.search_service.fetch_pages_batch(urls))
            
            # Use image capability from context to determine content mode
            is_image_mode = getattr(context, "image_input_supported", True)
            tab_ids = []
            if is_image_mode:
                from .render_vue import get_content_renderer
                renderer = await get_content_renderer()
                loop = asyncio.get_running_loop()
                tab_tasks = [
                    loop.run_in_executor(renderer._executor, renderer._prepare_tab_sync)
                    for _ in urls
                ]
                tab_ids = await asyncio.gather(*tab_tasks, return_exceptions=True)
                logger.debug(f"Instruct: Prepared {len(tab_ids)} tabs: {tab_ids}")
            
            crawl_results_list = await fetch_task
            
            if is_image_mode and tab_ids:
                theme_color = getattr(self.config, "theme_color", "#ef4444")
                render_tasks = []
                valid_pairs = []
                MAX_CHARS = 3000
                for i, (page_data, tab_id) in enumerate(zip(crawl_results_list, tab_ids)):
                    if isinstance(tab_id, Exception):
                        logger.warning(f"Instruct: Skip rendering page {i} due to tab error: {tab_id}")
                        continue
                    
                    # Truncate content to avoid excessive size
                    content = page_data.get("content", "")
                    if len(content) > MAX_CHARS:
                        content = content[:MAX_CHARS] + "\n\n...(content truncated for length)..."
                        page_data["content"] = content

                    if not content:
                        logger.warning(f"Instruct: Skip rendering page {i} due to empty content")
                        continue
                    
                    valid_pairs.append((i, page_data))
                    render_tasks.append(
                        loop.run_in_executor(
                            renderer._executor,
                            renderer._render_page_to_b64_sync,
                            {"title": page_data.get("title", "Page"), "content": content},
                            tab_id,
                            theme_color
                        )
                    )
                
                if render_tasks:
                    logger.debug(f"Instruct: Parallel rendering {len(render_tasks)} pages...")
                    screenshots = await asyncio.gather(*render_tasks, return_exceptions=True)
                    logger.debug(f"Instruct: Parallel rendering finished. Results count: {len(screenshots)}")
                    for j, (orig_idx, page_data) in enumerate(valid_pairs):
                         if j < len(screenshots) and not isinstance(screenshots[j], Exception):
                             crawl_results_list[orig_idx]["screenshot_b64"] = screenshots[j]

            for i, (url, tc_id) in enumerate(pending_crawls):
                page_data = crawl_results_list[i]
                title = page_data.get("title", "Unknown")
                
                # Update global context
                page_item = {
                    "_id": context.next_id(),
                    "_type": "page",
                    "title": page_data.get("title", "Page"),
                    "url": page_data.get("url", url),
                    "content": page_data.get("content", ""),
                    "is_crawled": True,
                }
                if page_data.get("screenshot_b64"):
                    page_item["screenshot_b64"] = page_data["screenshot_b64"]
                if page_data.get("raw_screenshot_b64"):
                    page_item["raw_screenshot_b64"] = page_data["raw_screenshot_b64"]
                if page_data.get("images"):
                    page_item["images"] = page_data["images"]
                    
                context.web_results.append(page_item)
                
                # Output for Context Assembly
                content_preview = page_data.get("content", "")[:500]
                results_for_context.append({
                     "id": tc_id, 
                     "name": "crawl_page", 
                     "content": f"Crawled '{title}' ({url}):\n{content_preview}..."
                })

        # 2. Searches
        if pending_searches:
            queries = [q for q, _ in pending_searches]
            logger.info(f"Instruct: Executing {len(queries)} searches via batch...")
            
            search_results_list = await self.search_service.search_batch(queries)
            
            for i, (query, tc_id) in enumerate(pending_searches):
                web_results = search_results_list[i]
                visible_results = [r for r in web_results if not r.get("_hidden")]
                
                # Update global context
                total_images = sum(len(item.get("images", []) or []) for item in web_results)
                logger.debug(f"Instruct: Search '{query}' returned {len(web_results)} items with {total_images} images total")
                for item in web_results:
                    item["_id"] = context.next_id()
                    if "type" in item:
                        item["_type"] = item["type"]
                    elif "_type" not in item:
                        item["_type"] = "search"
                    item["query"] = query
                    context.web_results.append(item)
                
                # Output for Context Assembly
                summary = f"Found {len(visible_results)} results for '{query}':\n"
                for r in visible_results[:5]:
                    summary += f"- {r.get('title')} ({r.get('url')}): {(r.get('content') or '')[:100]}...\n"
                
                results_for_context.append({
                     "id": tc_id, 
                     "name": "web_search", 
                     "content": summary
                })
                
        return results_for_context

    def _build_result(self, start_time, usage, content, tool_calls_count):
        model_cfg = self.config.get_model_config("instruct")
        model = model_cfg.get("model_name") or self.config.model_name
        
        trace = {
            "stage": "Instruct",
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
