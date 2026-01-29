"""
Agent Pipeline

Tool-calling agent that can autonomously use web_tool to search/screenshot.
Maximum 3 rounds of tool calls, up to 3 parallel calls per round.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from .definitions import get_web_tool, get_refuse_answer_tool, get_js_tool, AGENT_SYSTEM_PROMPT
from .stages.base import StageContext, StageResult
from .search import SearchService


@dataclass
class AgentSession:
    """Agent session with tool call tracking."""
    session_id: str
    user_query: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    conversation_history: List[Dict] = field(default_factory=list)
    messages: List[Dict] = field(default_factory=list)  # LLM conversation
    created_at: float = field(default_factory=time.time)
    
    # Round tracking (each round can have up to 3 parallel tool calls)
    round_count: int = 0
    
    # Image tracking
    user_image_count: int = 0  # Number of images from user input
    total_image_count: int = 0  # Total images including web screenshots
    
    # Time tracking
    search_time: float = 0.0  # Total time spent on search/screenshot
    llm_time: float = 0.0  # Total time spent on LLM calls
    first_llm_time: float = 0.0  # Time for first LLM call (understanding intent)
    
    # Usage tracking
    usage_totals: Dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    
    @property
    def call_count(self) -> int:
        """Total number of individual tool calls."""
        return len(self.tool_calls)
    
    @property
    def should_force_summary(self) -> bool:
        """Force summary after 3 rounds of tool calls."""
        return self.round_count >= 3


def parse_filter_syntax(query: str, max_count: int = 3):
    """
    Parse enhanced filter syntax supporting:
    - Chinese/English colons (: ：) and commas (, ，)
    - Multiple filters: "mcmod=2, github=1 : xxx" 
    - Index lists: "1, 2, 3 : xxx"
    - Max total selections
    
    Returns:
        filters: list of (filter_type, filter_value, count) tuples
                 filter_type: 'index' or 'link'
                 filter_value: int (for index) or str (for link match term)
                 count: how many to get (default 1)
        search_query: the actual search query
        error_msg: error message if exceeded max
    """
    import re
    
    # Skip filter parsing if query contains URL (has :// pattern)
    if re.search(r'https?://', query):
        return [], query.strip(), None
    
    # Normalize colons
    query = query.replace('：', ':')
    
    if ':' not in query:
        return [], query.strip(), None
    
    parts = query.split(':', 1)
    if len(parts) != 2:
        return [], query.strip(), None
    
    filter_part = parts[0].strip()
    search_query = parts[1].strip()
    
    if not filter_part or not search_query:
        return [], query.strip(), None
    
    # Parse filter expressions
    filters = []
    total_count = 0
    
    # Normalize commas
    filter_part = filter_part.replace('，', ',').replace('、', ',')
    filter_items = [f.strip() for f in filter_part.split(',') if f.strip()]
    
    for item in filter_items:
        # Check for "term=count" format (link filter)
        if '=' in item:
            term, count_str = item.split('=', 1)
            term = term.strip().lower()
            try:
                count = int(count_str.strip())
            except ValueError:
                count = 1
            if term and count > 0:
                filters.append(('link', term, count))
                total_count += count
        # Check for pure number (index filter)
        elif item.isdigit():
            idx = int(item)
            if 1 <= idx <= 10:
                filters.append(('index', idx, 1))
                total_count += 1
    
    if total_count > max_count:
        return None, search_query, f"⚠️ 最多选择{max_count}个结果"
    
    return filters, search_query, None


class AgentPipeline:
    """
    Tool-calling agent pipeline.
    
    Flow:
    1. 用户输入 → LLM (with tools)
    2. If tool_call: execute all tools in parallel → notify user with batched message → loop
    3. If call_count >= 3 rounds: force summary on next call
    4. Return final content
    """
    
    MAX_TOOL_ROUNDS = 3  # Maximum rounds of tool calls
    MAX_PARALLEL_TOOLS = 3  # Maximum parallel tool calls per round
    MAX_LLM_RETRIES = 3  # Maximum retries for empty API responses
    LLM_RETRY_DELAY = 1.0  # Delay between retries in seconds
    
    def __init__(
        self, 
        config: Any, 
        search_service: SearchService,
        send_func: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        self.config = config
        self.search_service = search_service
        self.send_func = send_func
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
    
    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict],
        images: List[str] = None,
        model_name: str = None,
    ) -> Dict[str, Any]:
        """Execute agent with tool-calling loop."""
        start_time = time.time()
        
        # Get model config
        model_cfg = self.config.get_model_config("main")
        model = model_name or model_cfg.model_name or self.config.model_name
        
        client = AsyncOpenAI(
            base_url=model_cfg.base_url or self.config.base_url,
            api_key=model_cfg.api_key or self.config.api_key
        )
        
        # Create session
        session = AgentSession(
            session_id=str(time.time()),
            user_query=user_input,
            conversation_history=conversation_history.copy()
        )
        
        # Create context for results
        context = StageContext(
            user_input=user_input,
            images=images or [],
            conversation_history=conversation_history,
        )
        
        # Build initial messages
        language = getattr(self.config, "language", "Simplified Chinese")
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        system_prompt = AGENT_SYSTEM_PROMPT + f"\n\n用户要求的语言: {language}\n当前时间: {current_time}"
        
        # Build user content with images if provided
        user_image_count = len(images) if images else 0
        session.user_image_count = user_image_count
        session.total_image_count = user_image_count
        
        if images:
            user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_input}]
            for img_b64 in images:
                url = f"data:image/jpeg;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_input
        
        session.messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Add conversation history (previous turns) before current user message
        # This enables continuous conversation context
        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    session.messages.append({"role": role, "content": content})

        # Add current user message
        session.messages.append({"role": "user", "content": user_content})
        
        # Add image source hint for user images
        if user_image_count > 0:
            if user_image_count == 1:
                hint = "第1张图片来自用户输入，请将这张图片作为用户输入的参考"
            else:
                hint = f"第1-{user_image_count}张图片来自用户输入，请将这{user_image_count}张图片作为用户输入的参考"
            session.messages.append({"role": "system", "content": hint})
        
        # Tool definitions
        web_tool = get_web_tool()
        refuse_tool = get_refuse_answer_tool()
        js_tool = get_js_tool()
        tools = [web_tool, refuse_tool, js_tool]

        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        final_content = ""
        
        # Send initial status notification
        if self.send_func:
            try:
                await self.send_func("💭 何意味...")
            except Exception as e:
                logger.warning(f"AgentPipeline: Failed to send initial notification: {e}")
        
        # Agent loop
        while True:
            # Check if we need to force summary (no tools)
            if session.should_force_summary:
                logger.info(f"AgentPipeline: Max tool rounds ({self.MAX_TOOL_ROUNDS}) reached, forcing summary")
                # Add context message about collected info
                if context.web_results:
                    context_msg = self._format_web_context(context)
                    session.messages.append({
                        "role": "system", 
                        "content": f"你已经完成了{session.call_count}次工具调用。请基于已收集的信息给出最终回答。\n\n{context_msg}"
                    })
                
                
                # Final call without tools (with retry)
                response = None
                for retry in range(self.MAX_LLM_RETRIES):
                    try:
                        response = await client.chat.completions.create(
                            model=model,
                            messages=session.messages,
                            temperature=self.config.temperature,
                        )
                        
                        if response.usage:
                            usage_totals["input_tokens"] += response.usage.prompt_tokens or 0
                            usage_totals["output_tokens"] += response.usage.completion_tokens or 0
                        
                        # Check for valid response
                        if response.choices:
                            break  # Success, exit retry loop
                        
                        # Empty choices, retry
                        logger.warning(f"AgentPipeline: Empty choices in force-summary (attempt {retry + 1}/{self.MAX_LLM_RETRIES}): {response}")
                        if retry < self.MAX_LLM_RETRIES - 1:
                            await asyncio.sleep(self.LLM_RETRY_DELAY)
                    except Exception as e:
                        logger.warning(f"AgentPipeline: LLM error (attempt {retry + 1}/{self.MAX_LLM_RETRIES}): {e}")
                        if retry < self.MAX_LLM_RETRIES - 1:
                            await asyncio.sleep(self.LLM_RETRY_DELAY)
                        else:
                            return {
                                "llm_response": f"Error: {e}",
                                "success": False,
                                "error": str(e),
                                "stats": {"total_time": time.time() - start_time}
                            }
                
                # Final check after all retries
                if not response or not response.choices:
                    logger.error(f"AgentPipeline: All retries failed for force-summary")
                    return {
                        "llm_response": "抱歉，AI 服务返回了空响应，请稍后重试。",
                        "success": False,
                        "error": "Empty response from API after retries",
                        "stats": {"total_time": time.time() - start_time},
                        "usage": usage_totals,
                    }
                
                final_content = response.choices[0].message.content or ""
                break
            
            # Normal call with tools (with retry)
            llm_start = time.time()
            response = None
            
            for retry in range(self.MAX_LLM_RETRIES):
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=session.messages,
                        temperature=self.config.temperature,
                        tools=tools,
                        tool_choice="auto",
                    )
                    
                    # Check for valid response
                    if response.choices:
                        break  # Success, exit retry loop
                    
                    # Empty choices, retry
                    logger.warning(f"AgentPipeline: Empty choices (attempt {retry + 1}/{self.MAX_LLM_RETRIES}): {response}")
                    if retry < self.MAX_LLM_RETRIES - 1:
                        await asyncio.sleep(self.LLM_RETRY_DELAY)
                except Exception as e:
                    logger.warning(f"AgentPipeline: LLM error (attempt {retry + 1}/{self.MAX_LLM_RETRIES}): {e}")
                    if retry < self.MAX_LLM_RETRIES - 1:
                        await asyncio.sleep(self.LLM_RETRY_DELAY)
                    else:
                        logger.error(f"AgentPipeline: All retries failed: {e}")
                        return {
                            "llm_response": f"Error: {e}",
                            "success": False,
                            "error": str(e),
                            "stats": {"total_time": time.time() - start_time}
                        }
            
            llm_duration = time.time() - llm_start
            session.llm_time += llm_duration
            
            # Track first LLM call time (理解用户意图)
            if session.call_count == 0 and session.first_llm_time == 0:
                session.first_llm_time = llm_duration
            
            # Final check after all retries
            if not response or not response.choices:
                logger.error(f"AgentPipeline: All retries failed, empty choices")
                return {
                    "llm_response": "抱歉，AI 服务返回了空响应，请稍后重试。",
                    "success": False,
                    "error": "Empty response from API after retries",
                    "stats": {"total_time": time.time() - start_time},
                    "usage": usage_totals,
                }
            
            if response.usage:
                usage_totals["input_tokens"] += response.usage.prompt_tokens or 0
                usage_totals["output_tokens"] += response.usage.completion_tokens or 0
            
            message = response.choices[0].message
            
            # Check for tool calls
            if not message.tool_calls:
                # Model chose to answer directly
                final_content = message.content or ""
                logger.info(f"AgentPipeline: Model answered directly after {session.call_count} tool calls")
                break
            
            # Add assistant message with tool calls
            session.messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in message.tool_calls
                ]
            })
            
            # Execute all tool calls in parallel
            tool_tasks = []
            tool_call_ids = []
            tool_call_names = []
            tool_call_args_list = []
            
            for tool_call in message.tool_calls:
                tc_id = tool_call.id
                func_name = tool_call.function.name
                
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                
                tool_call_ids.append(tc_id)
                tool_call_names.append(func_name)
                tool_call_args_list.append(args)
                logger.info(f"AgentPipeline: Queueing tool '{func_name}' with args: {args}")
            
            # Check for refuse_answer first (handle immediately)
            for idx, func_name in enumerate(tool_call_names):
                if func_name == "refuse_answer":
                    args = tool_call_args_list[idx]
                    reason = args.get("reason", "Refused")
                    context.should_refuse = True
                    context.refuse_reason = reason
                    
                    session.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_ids[idx],
                        "content": f"已拒绝回答: {reason}"
                    })
                    
                    return {
                        "llm_response": "",
                        "success": True,
                        "refuse_answer": True,
                        "refuse_reason": reason,
                        "stats": {"total_time": time.time() - start_time},
                        "usage": usage_totals,
                    }
            
            # Execute web_tool calls in parallel
            search_start = time.time()
            tasks_to_run = []
            task_indices = []
            
            for idx, func_name in enumerate(tool_call_names):
                if func_name == "web_tool":
                    tasks_to_run.append(self._execute_web_tool(tool_call_args_list[idx], context))
                    task_indices.append(idx)
                elif func_name == "js_executor":
                    tasks_to_run.append(self._execute_js_tool(tool_call_args_list[idx], context))
                    task_indices.append(idx)

            # Run all web_tool calls in parallel
            if tasks_to_run:
                results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
            else:
                results = []
            
            session.search_time += time.time() - search_start
            
            # Process results and collect notifications
            notifications = []
            result_map = {}  # Map task index to result
            
            for i, result in enumerate(results):
                task_idx = task_indices[i]
                if isinstance(result, Exception):
                    result_map[task_idx] = {"summary": f"执行失败: {result}", "results": []}
                else:
                    result_map[task_idx] = result
            
            # Add all tool results to messages and collect notifications
            for idx, func_name in enumerate(tool_call_names):
                tc_id = tool_call_ids[idx]
                args = tool_call_args_list[idx]
                
                if func_name == "web_tool":
                    result = result_map.get(idx, {"summary": "未执行", "results": []})

                    # Track tool call
                    session.tool_calls.append({"name": func_name, "args": args})
                    session.tool_results.append(result)

                    # Collect notification with appropriate emoji
                    summary = result['summary']
                    if "失败" in summary or "Error" in summary:
                        notifications.append(f"⚠️ {summary}")
                    else:
                        notifications.append(f"🔍 {summary}")
                    
                    # Add tool result to messages
                    formatted_results = ""
                    if result.get("results"):
                        formatted_results = "\n\n详细结果:\n"
                        for i, r in enumerate(result["results"]):
                            title = r.get("title", "无标题")
                            url = r.get("url", "")
                            snippet = r.get("snippet", "") or r.get("content", "") or ""
                            # Limit snippet length
                            snippet = snippet[:300] + "..." if len(snippet) > 300 else snippet
                            formatted_results += f"{i+1}. [{title}]({url})\n   摘要: {snippet}\n\n"

                    result_content = f"搜索完成: {result['summary']}\n\n找到 {len(result.get('results', []))} 个结果{formatted_results}"
                    session.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result_content
                    })
                    
                    # Add image source hint for web screenshots
                    screenshot_count = result.get("screenshot_count", 0)
                    if screenshot_count > 0:
                        start_idx_img = session.total_image_count + 1
                        end_idx_img = session.total_image_count + screenshot_count
                        session.total_image_count = end_idx_img
                        
                        source_desc = result.get("source_desc", "网页截图")
                        if start_idx_img == end_idx_img:
                            hint = f"第{start_idx_img}张图片来自{source_desc}，作为查询的参考资料"
                        else:
                            hint = f"第{start_idx_img}-{end_idx_img}张图片来自{source_desc}，作为查询的参考资料"
                        session.messages.append({"role": "system", "content": hint})
                else:
                    # Unknown tool
                    session.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"Unknown tool: {func_name}"
                    })
            
            # Send batched notification (up to 3 lines)
            if self.send_func and notifications:
                try:
                    # Join notifications with newlines, max 3 lines
                    notification_msg = "\n".join(notifications[:3])
                    await self.send_func(notification_msg)
                except Exception as e:
                    logger.warning(f"AgentPipeline: Failed to send notification: {e}")
            
            # Increment round count after processing all tool calls in this round
            if tasks_to_run:
                session.round_count += 1
        
        # Build final response
        total_time = time.time() - start_time
        stats = {"total_time": total_time}
        
        # Update conversation history
        conversation_history.append({"role": "user", "content": user_input})
        conversation_history.append({"role": "assistant", "content": final_content})
        
        stages_used = self._build_stages_ui(session, context, usage_totals, total_time)
        logger.info(f"AgentPipeline: Built stages_used = {stages_used}")
        
        return {
            "llm_response": final_content,
            "success": True,
            "stats": stats,
            "model_used": model,
            "conversation_history": conversation_history,
            "usage": usage_totals,
            "web_results": context.web_results,
            "tool_calls_count": session.call_count,
            "stages_used": stages_used,
        }
    
    async def _execute_web_tool(self, args: Dict, context: StageContext) -> Dict[str, Any]:
        """执行 web_tool - 复用 /w 逻辑，支持过滤器语法"""
        query = args.get("query", "")
        
        # 1. URL 截图模式 - 检测 query 中是否包含 URL
        url_match = re.search(r'https?://\S+', query)
        if url_match:
            url = url_match.group(0)
            # Send URL screenshot notification
            if self.send_func:
                try:
                    await self.send_func(f"🌐 正在获取页面...")
                except Exception:
                    pass
            
            logger.info(f"AgentPipeline: Screenshot URL with content: {url}")
            # Use screenshot_with_content to get both screenshot and text
            result = await self.search_service.screenshot_with_content(url)
            screenshot_b64 = result.get("screenshot_b64")
            content = result.get("content", "")
            title = result.get("title", "")
            
            if screenshot_b64:
                context.web_results.append({
                    "_id": context.next_id(),
                    "_type": "page",
                    "url": url,
                    "title": title or "Screenshot",
                    "screenshot_b64": screenshot_b64,
                    "content": content,  # Text content for LLM
                })
                return {
                    "summary": f"已截图: {url[:50]}{'...' if len(url) > 50 else ''}",
                    "results": [{"_type": "screenshot", "url": url}],
                    "screenshot_count": 1,
                    "source_desc": f"URL截图 ({url[:30]}...)"
                }
            return {
                "summary": f"截图失败: {url[:50]}",
                "results": [],
                "screenshot_count": 0
            }
        
        # 2. 解析过滤器语法
        filters, search_query, error = parse_filter_syntax(query, max_count=3)
        
        if error:
            return {"summary": error, "results": []}
        
        # 3. 如果有过滤器，发送搜索+截图预告
        if filters and self.send_func:
            try:
                # Build filter description
                filter_desc_parts = []
                for f_type, f_val, f_count in filters:
                    if f_type == 'index':
                        filter_desc_parts.append(f"第{f_val}个")
                    else:
                        filter_desc_parts.append(f"{f_val}={f_count}")
                filter_desc = ", ".join(filter_desc_parts)
                await self.send_func(f"🔍 正在搜索 \"{search_query}\" 并匹配 [{filter_desc}]...")
            except Exception:
                pass
        
        logger.info(f"AgentPipeline: Searching for: {search_query}")
        results = await self.search_service.search(search_query)
        visible = [r for r in results if not r.get("_hidden")]
        
        # Add search results to context
        for r in results:
            r["_id"] = context.next_id()
            if "_type" not in r:
                r["_type"] = "search"
            r["query"] = search_query
            context.web_results.append(r)
        
        # 4. 如果有过滤器，截图匹配的链接
        if filters:
            urls = self._collect_filter_urls(filters, visible)
            if urls:
                logger.info(f"AgentPipeline: Taking screenshots with content of {len(urls)} URLs")
                # Use screenshot_with_content to get both screenshot and text
                screenshot_tasks = [self.search_service.screenshot_with_content(u) for u in urls]
                results = await asyncio.gather(*screenshot_tasks)
                
                # Add screenshots and content to context
                successful_count = 0
                for url, result in zip(urls, results):
                    screenshot_b64 = result.get("screenshot_b64") if isinstance(result, dict) else None
                    content = result.get("content", "") if isinstance(result, dict) else ""
                    title = result.get("title", "") if isinstance(result, dict) else ""
                    
                    if screenshot_b64:
                        successful_count += 1
                        # Find and update the matching result
                        for r in context.web_results:
                            if r.get("url") == url:
                                r["screenshot_b64"] = screenshot_b64
                                r["content"] = content  # Text content for LLM
                                r["title"] = title or r.get("title", "")
                                r["_type"] = "page"
                                break
                
                return {
                    "summary": f"搜索 \"{search_query}\" 并截图 {successful_count} 个匹配结果",
                    "results": [{"url": u, "_type": "page"} for u in urls],
                    "screenshot_count": successful_count,
                    "source_desc": f"搜索 \"{search_query}\" 的网页截图"
                }
        
        # 5. 普通搜索模式 (无截图)
        return {
            "summary": f"搜索 \"{search_query}\" 找到 {len(visible)} 条结果",
            "results": visible,
            "screenshot_count": 0
        }
    
    async def _execute_js_tool(self, args: Dict, context: StageContext) -> Dict[str, Any]:
        """执行 JS 代码工具"""
        script = args.get("script", "")
        if not script:
             return {"summary": "JS执行失败: 代码为空", "results": []}

        if self.send_func:
            try:
                await self.send_func("💻 正在执行JavaScript代码...")
            except: pass

        logger.info(f"AgentPipeline: Executing JS script: {script[:50]}...")
        result = await self.search_service.execute_script(script)

        # 格式化结果
        success = result.get("success", False)
        output = result.get("result", None)
        error = result.get("error", None)
        url = result.get("url", "")
        title = result.get("title", "")

        # Add to context
        context.web_results.append({
            "_id": context.next_id(),
            "_type": "js_result",
            "url": url,
            "title": title or "JS Execution",
            "script": script,
            "output": str(output) if success else str(error),
            "success": success,
            "content": f"Script: {script}\n\nOutput: {output}" if success else f"Error: {error}"
        })

        if success:
            summary = f"JS执行成功 (返回: {str(output)[:50]}...)"
            return {
                "summary": summary,
                "results": [{"_type": "js_result", "url": url}],
                "screenshot_count": 0,
                "full_output": str(output),  # Return full output for LLM
                "success": True
            }
        else:
            return {
                "summary": f"JS执行失败: {str(error)[:50]}",
                "results": [],
                "screenshot_count": 0,
                "full_output": f"JS Execution Failed: {error}",
                "success": False,
                "error": str(error)
            }


    def _collect_filter_urls(self, filters: List, visible: List[Dict]) -> List[str]:
        """Collect URLs based on filter specifications."""
        urls = []
        
        for filter_type, filter_value, count in filters:
            if filter_type == 'index':
                idx = filter_value - 1  # Convert to 0-based
                if 0 <= idx < len(visible):
                    url = visible[idx].get("url", "")
                    if url and url not in urls:
                        urls.append(url)
            else:
                # Link filter
                found_count = 0
                for res in visible:
                    url = res.get("url", "")
                    title = res.get("title", "")
                    # Match filter against both URL and title
                    if (filter_value in url.lower() or filter_value in title.lower()) and url not in urls:
                        urls.append(url)
                        found_count += 1
                        if found_count >= count:
                            break
        
        return urls
    
    def _format_web_context(self, context: StageContext) -> str:
        """Format web results for summary context."""
        if not context.web_results:
            return ""
        
        lines = ["## 已收集的信息\n"]
        for r in context.web_results:
            idx = r.get("_id", "?")
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")[:500] if r.get("content") else ""
            has_screenshot = "有截图" if r.get("screenshot_b64") else ""
            
            lines.append(f"[{idx}] {title}")
            if url:
                lines.append(f"    URL: {url}")
            if has_screenshot:
                lines.append(f"    {has_screenshot}")
            if content:
                lines.append(f"    摘要: {content[:200]}...")
            lines.append("")
        
        return "\n".join(lines)
    
    def _build_stages_ui(self, session: AgentSession, context: StageContext, usage_totals: Dict, total_time: float) -> List[Dict[str, Any]]:
        """Build stages UI for rendering - compatible with App.vue flow section.
        
        Flow: Instruct (意图) → Search (搜索) → Summary (总结)
        """
        stages = []
        
        # Get model config for pricing
        model_cfg = self.config.get_model_config("main")
        model_name = model_cfg.model_name or self.config.model_name
        input_price = getattr(model_cfg, "input_price", 0) or 0
        output_price = getattr(model_cfg, "output_price", 0) or 0
        
        # 1. Instruct Stage (理解用户意图 - 第一次LLM调用)
        if session.first_llm_time > 0:
            # Estimate tokens for first call (rough split based on proportion)
            # Since we track total usage, we approximate first call as ~40% of total
            first_call_ratio = 0.4 if session.call_count > 0 else 1.0
            instruct_input = int(usage_totals.get("input_tokens", 0) * first_call_ratio)
            instruct_output = int(usage_totals.get("output_tokens", 0) * first_call_ratio)
            instruct_cost = (instruct_input * input_price + instruct_output * output_price) / 1_000_000
            
            stages.append({
                "name": "Instruct",
                "model": model_name,
                "provider": model_cfg.model_provider or "OpenRouter",
                "description": "理解用户意图",
                "time": session.first_llm_time,
                "usage": {"input_tokens": instruct_input, "output_tokens": instruct_output},
                "cost": instruct_cost,
            })
        
        # 2. Search Stage (搜索) / Browser JS Stage
        if session.tool_calls:
            # Collect all search descriptions and check for JS executor calls
            search_descriptions = []
            js_calls = []

            for tc, result in zip(session.tool_calls, session.tool_results):
                if tc.get("name") == "js_executor":
                    # Collect JS execution info
                    js_calls.append({
                        "script": tc.get("args", {}).get("script", ""),
                        "output": result.get("full_output", result.get("summary", "")),
                        "url": result.get("results", [{}])[0].get("url", "") if result.get("results") else "",
                        "success": result.get("success", True), # Default to True if not present
                        "error": result.get("error", "")
                    })
                else:
                    desc = result.get("summary", "")
                    if desc:
                        search_descriptions.append(desc)

            # Add Search stage if there are search calls
            if search_descriptions:
                stages.append({
                    "name": "Search",
                    "model": "",
                    "provider": "Web",
                    "description": " → ".join(search_descriptions),
                    "time": session.search_time,
                })

            # Add Browser JS stage for each JS call
            for js_call in js_calls:
                stages.append({
                    "name": "browser_js",
                    "model": "",
                    "provider": "Browser",
                    "description": "JavaScript Execution",
                    "script": js_call["script"],
                    "output": js_call["output"],
                    "url": js_call["url"],
                    "success": js_call.get("success"),
                    "error": js_call.get("error"),
                    "time": 0,  # JS execution time is included in search_time
                })
        
        # 3. Summary Stage (总结)
        # Calculate remaining tokens after instruct
        summary_ratio = 0.6 if session.call_count > 0 else 0.0
        summary_input = int(usage_totals.get("input_tokens", 0) * summary_ratio)
        summary_output = int(usage_totals.get("output_tokens", 0) * summary_ratio)
        summary_cost = (summary_input * input_price + summary_output * output_price) / 1_000_000
        summary_time = session.llm_time - session.first_llm_time
        
        if summary_time > 0 or session.call_count > 0:
            stages.append({
                "name": "Summary",
                "model": model_name,
                "provider": model_cfg.model_provider or "OpenRouter",
                "description": f"生成回答 ({session.call_count} 次工具调用)",
                "time": max(0, summary_time),
                "usage": {"input_tokens": summary_input, "output_tokens": summary_output},
                "cost": summary_cost,
            })
        
        return stages
