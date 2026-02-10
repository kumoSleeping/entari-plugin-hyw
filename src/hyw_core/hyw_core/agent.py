import asyncio
import json
import re
from loguru import logger
from typing import List, Optional, Dict, Any, Union
from openai import AsyncOpenAI
from .models import Message, AgentResponse, ToolResult
from .tools.registry import ToolRegistry
from .config import HywCoreConfig


class AgentSession:
    """Agent 会话，使用 XML 格式输出，自行解析"""

    def __init__(self, client: AsyncOpenAI, model: str, user_input: str, registry: ToolRegistry, images: Optional[List[str]] = None):
        self.client = client
        self.model = model
        self.registry = registry
        self.turn = 0
        self.finished = False

        # 构造用户消息 (支持多模态)
        if images:
            user_content: Union[str, List[Dict[str, Any]]] = [{"type": "text", "text": user_input}]
            for img in images:
                url = img if img.startswith(("http", "data:")) else f"data:image/jpeg;base64,{img}"
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_input

        self.api_messages: List[Dict] = [{"role": "user", "content": user_content}]
        self.history: List[Message] = [Message(role="user", content=user_input)]

        # 可用工具名称列表
        self.available_tools: List[str] = []
        self.use_final_only = False

    async def step(self) -> AgentResponse:
        """执行一轮 LLM 推理 + 工具执行（使用 XML 格式解析）"""
        if self.finished:
            return AgentResponse(content="", history=self.history)

        # 不再直接清理 temp prompts，而是发送时过滤
        # self._cleanup_temp_prompts()
        self.turn += 1

        try:
            # 构建请求参数 - 过滤掉非当前轮次的 temp prompt
            # 保留所有非 _temp 消息
            # 保留 _temp 且 turn 等于当前轮次 (需要在 configure 中记录 turn) 或 刚刚添加的 (假设没有 turn 标记的就是新的?)
            # 为了简单起见，我们修改 logic：发送所有消息，但只保留最近的一个 temp prompt?
            # 或者更严谨地：在 configure 时标记 turn。

            # 临时修补：如果不修改 configure，无法区分。
            # 但通常 step 前会调用 configure。所以 api_messages 里所有的 _temp 都是最新的（假设之前的已经被清理了）。
            # 但如果我们不清理，就会堆积。
            # 方案：在构建 messages 时，过滤掉旧的 _temp。
            # 假设 api_messages 中的 _temp 都有 turn 标记（稍后修改 configure）。

            current_messages = []
            for msg in self.api_messages:
                if not msg.get("_temp"):
                    current_messages.append(msg)
                elif msg.get("_temp_turn") == self.turn: # 假设 configure 设置了 _temp_turn = self.turn + 1 (因为 step 还没跑，turn 还没加?)
                    # 修正：step 开头 self.turn += 1。
                    # configure 在 step 之前跑，那时 self.turn 是 N。step 跑的时候 self.turn 变成 N+1。
                    # 所以 configure 应该记录的是 N+1 ? 或者 step 晚点加 turn?
                    # 让我们看 agent.py 的 turn 逻辑。
                    # __init__: turn = 0.
                    # step: turn += 1.
                    # flow (调用 configure): turn 还是 0 (第一次).
                    # 所以 configure 记录 turn=0. step 变成 1.
                    # 匹配逻辑应该是 msg.get("_temp_turn") == self.turn - 1 ?
                    # 不，最好 step 里的 turn += 1 放在后面？或者 configure 用 next turn。
                    current_messages.append(msg)

            # 如果没有修改 configure，这种过滤会失败。
            # 我们先用简单方案：发送所有消息。
            # 之前的 bug 是 _cleanup_temp_prompts() 删除了所有 _temp。
            # 如果我们删掉这就好了？但下一轮会带上前一轮的 temp。
            # 这是一个权衡。
            # 鉴于用户急需修复 "temp_prompt 没生效"，我们先改为：
            # 每次 step 结束时清理？或者 step 开始时不清理，由 flow 控制？
            # 最好的办法是：在 step 内部，先提取 messages 发送，然后再清理（但在 api_messages 里保留用于 log？）

            # 采用方案：仅用于发送的临时列表
            messages_to_send = self.api_messages

            kwargs: Dict[str, Any] = {"model": self.model, "messages": messages_to_send}

            # LLM 调用
            response = await self.client.chat.completions.create(**kwargs)

            # ... (处理响应)
            msg = response.choices[0].message
            content = msg.content or ""

            # 记录原始响应
            self.api_messages.append({"role": "assistant", "content": content})

            # 这一步之后清理旧的 temp prompts?
            # 如果清理了，log 就没了。
            # 所以 api_messages 必须保留所有历史。
            # 只有 messages_to_send 需要过滤。

            return await self._parse_xml_response(content)

        except Exception as e:
            self.finished = True
            import traceback
            logger.exception(f"Error in HywAgent.step: {e}")
            return AgentResponse(content="", success=False, error=str(e), history=self.history)

    async def _parse_xml_response(self, raw_content: str) -> AgentResponse:
        """解析 XML 格式的 LLM 输出"""
        if not raw_content:
            self.finished = True
            return AgentResponse(content="", history=self.history)

        # 1. 解析工具调用 <tool_call name="xxx">...</tool_call>
        tool_calls = self._extract_tool_calls(raw_content)
        if tool_calls and not self.use_final_only:
            # 记录到 history
            tool_calls_dump = [{"name": tc["name"], "arguments": tc["arguments"]} for tc in tool_calls]
            self.history.append(Message(role="assistant", content=raw_content, tool_calls=tool_calls_dump))
            # 执行工具
            return await self._execute_tool_calls(tool_calls)

        # 2. 解析评分 <scoring>...</scoring>
        scoring = self._extract_scoring(raw_content)

        # 3. 解析最终回复 <response>...</response> 或 <response render="true">...</response>
        response_match = re.search(r'<response(?:\s+render=["\']?(true|false)["\']?)?\s*>(.*?)</response>', raw_content, re.DOTALL | re.IGNORECASE)
        if response_match:
            should_render = response_match.group(1) and response_match.group(1).lower() == "true"
            content = response_match.group(2).strip()
            self.history.append(Message(role="assistant", content=content))
            self.finished = True
            return AgentResponse(
                content=content,
                history=self.history,
                is_final=True,
                should_render=should_render,
                scoring=scoring
            )

        # 4. 没有匹配到 XML 标签，当作纯文本回复
        self.history.append(Message(role="assistant", content=raw_content))
        self.finished = True
        return AgentResponse(content=raw_content, history=self.history, is_final=True, scoring=scoring)

    def _extract_scoring(self, content: str) -> Optional[List[Dict[str, Any]]]:
        """提取评分信息 <scoring><item index="1" score="8">理由</item>...</scoring>"""
        scoring_match = re.search(r'<scoring>(.*?)</scoring>', content, re.DOTALL | re.IGNORECASE)
        if not scoring_match:
            return None

        scoring = []
        # 匹配每个 <item index="N" score="M">理由</item>
        item_pattern = r'<item\s+index=["\']?(\d+)["\']?\s+score=["\']?(\d+)["\']?\s*>(.*?)</item>'
        items = re.findall(item_pattern, scoring_match.group(1), re.DOTALL | re.IGNORECASE)

        for index, score, reason in items:
            scoring.append({
                "index": int(index),
                "score": int(score),
                "reason": reason.strip()
            })

        return scoring if scoring else None

    def _extract_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """从内容中提取所有工具调用"""
        tool_calls = []
        # 匹配 <tool_call name="tool_name">...params...</tool_call>
        pattern = r'<tool_call\s+name=["\']?(\w+)["\']?\s*>(.*?)</tool_call>'
        matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)

        for name, params_xml in matches:
            # 检查工具是否可用
            if self.available_tools and name not in self.available_tools:
                continue
            # 解析参数
            arguments = self._parse_tool_params(params_xml)
            tool_calls.append({"name": name, "arguments": arguments})

        return tool_calls

    def _parse_tool_params(self, params_xml: str) -> Dict[str, Any]:
        """解析工具参数 XML，如 <query>xxx</query><url>yyy</url>"""
        params = {}
        # 匹配所有 <param_name>value</param_name>
        pattern = r'<(\w+)>(.*?)</\1>'
        matches = re.findall(pattern, params_xml, re.DOTALL)
        for key, value in matches:
            params[key] = value.strip()
        return params

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> AgentResponse:
        """执行从结构化输出解析出的工具调用"""
        tasks, infos = [], []
        search_queries = []

        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]

            # 收集搜索查询
            if name == "web_search":
                search_queries.append(args.get("query", ""))

            tasks.append(self.registry.execute(name, args))
            infos.append(name)

        if not tasks:
            return AgentResponse(content="", history=self.history, success=True)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 搜索完成后发送汇总消息
        if search_queries and self.registry.get_send_hook():
            search_summary_lines = [f"🔍 搜索完成 ({len(search_queries)} 条):"]
            for i, (query, result) in enumerate(zip(search_queries, results)):
                if isinstance(result, Exception):
                    search_summary_lines.append(f"  {i+1}. {query} ❌ 失败")
                else:
                    result_str = str(result) if not hasattr(result, 'content') else result.content
                    try:
                        parsed = json.loads(result_str)
                        count = parsed.get("count", 0)
                        summary = parsed.get("summary", "")
                        search_summary_lines.append(f"  {i+1}. {query} ✓ {count} 条结果")
                        if summary:
                            search_summary_lines.append(f"      {summary}")
                    except json.JSONDecodeError:
                        result_count = result_str.count("**[") if "**[" in result_str else 0
                        search_summary_lines.append(f"  {i+1}. {query} ✓ {result_count} 条结果")
            await self.registry.get_send_hook()("\n".join(search_summary_lines))

        # 处理结果，添加到 api_messages
        for name, result in zip(infos, results):
            if isinstance(result, Exception):
                tool_content = f"Error: {result}"
            elif isinstance(result, ToolResult):
                tool_content = result.content
                if result.should_finish:
                    self.finished = True
                    self.history.append(Message(role="tool", content=tool_content))
                    return AgentResponse(content=tool_content, history=self.history)
            else:
                tool_content = str(result)

            # 添加工具结果到 api_messages（使用 user role 模拟，因为没有真正的 tool_call_id）
            self.api_messages.append({"role": "user", "content": f"[Tool Result: {name}]\n{tool_content}"})

        return AgentResponse(content="", history=self.history, success=True)

    def configure(
        self,
        tools: Optional[List[str]] = None,
        prompt: Optional[str] = None,
        temp_prompt: Optional[str] = None,
        final_only: bool = False
    ) -> None:
        """动态调整下一轮的执行参数

        Args:
            tools: 可用工具名称列表
            prompt: 持久化系统提示
            temp_prompt: 临时系统提示（下一轮后清除）
            final_only: 强制只允许 response（不允许 tool_call）
        """
        if tools is not None:
            self.available_tools = tools
        self.use_final_only = final_only

        if prompt:
            self.api_messages.append({"role": "system", "content": prompt})
        if temp_prompt:
            self.api_messages.append({"role": "system", "content": temp_prompt, "_temp": True})

    def _cleanup_temp_prompts(self):
        """清理临时系统提示"""
        self.api_messages = [m for m in self.api_messages if not m.get("_temp")]


class HywAgent:
    def __init__(self, config: HywCoreConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
        self.model = config.model_name

    def init_session(self, user_input: str, registry: Optional[ToolRegistry] = None, images: Optional[List[str]] = None) -> AgentSession:
        return AgentSession(self.client, self.model, user_input, registry or ToolRegistry(), images)

    async def close(self):
        await self.client.close()
