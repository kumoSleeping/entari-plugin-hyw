import asyncio
import re
from loguru import logger
from typing import List, Optional, Dict, Any, Union
from openai import AsyncOpenAI
from .models import Message, AgentResponse, ToolResult
from .tools.registry import ToolRegistry
from .config import HywCoreConfig


class AgentSession:
    """Agent 会话，使用 XML 格式输出，自行解析"""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        user_input: str,
        registry: ToolRegistry,
        images: Optional[List[str]] = None,
        temperature: Optional[float] = None,
    ):
        self.client = client
        self.model = model
        self.registry = registry
        self.turn = 0
        self.finished = False
        self.temperature = temperature

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

        self.turn += 1

        try:
            messages_to_send = self.api_messages
            kwargs: Dict[str, Any] = {"model": self.model, "messages": messages_to_send}
            if self.temperature is not None:
                kwargs["temperature"] = self.temperature
                logger.info(f"[Sampling] 第{self.turn}轮调用参数: temperature={self.temperature:.2f}")
            else:
                logger.info(f"[Sampling] 第{self.turn}轮调用参数: temperature=<模型默认>")

            # LLM 调用
            response = await self.client.chat.completions.create(**kwargs)

            msg = response.choices[0].message
            content = msg.content or ""

            # 记录原始响应
            self.api_messages.append({"role": "assistant", "content": content})

            return await self._parse_xml_response(content)

        except Exception as e:
            self.finished = True
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
            return await self._execute_tool_calls(tool_calls, raw_content)

        # 2. 解析评分 <scoring>...</scoring>
        scoring = self._extract_scoring(raw_content)

        # 3. 作为最终回复处理：优先提取 execution_content，避免暴露内部思考结构
        final_content = self._extract_final_content(raw_content)

        self.history.append(Message(role="assistant", content=final_content))
        self.finished = True

        # 自动检测是否包含 Markdown
        should_render = self._has_markdown(final_content)

        return AgentResponse(
            content=final_content,
            history=self.history,
            is_final=True,
            should_render=should_render,
            scoring=scoring
        )

    def _has_markdown(self, text: str) -> bool:
        """检测文本是否包含 Markdown 语法"""
        if not text:
            return False
        # 字数阈值：超过 200 字直接触发渲染
        if len(text.strip()) > 200:
            return True

        # 常见的 Markdown 模式
        patterns = [
            r'^#{1,6}\s',           # 标题 (行首)
            r'^[\*\-]\s',           # 无序列表 (行首)
            r'^\d+\.\s',            # 有序列表 (行首)
            r'```',                 # 代码块
            r'`[^`]+`',             # 行内代码
            r'\[.*?\]\(.*?\)',      # 链接
            r'(\*\*|__|\*|_).*(\*\*|__|\*|_)', # 粗体/斜体 (简单检测成对标记)
            r'^>\s',                # 引用 (行首)
            r'^---\s*$',            # 分割线 (行首)
            r'\|.*\|'               # 表格 (简单检测)
        ]

        for pattern in patterns:
            if re.search(pattern, text, re.MULTILINE):
                return True

        return False

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

    def _extract_final_content(self, content: str) -> str:
        """提取最终可展示内容，过滤 response_logic 内部结构。"""
        if not content:
            return ""

        # scoring 仅用于结构化展示，不应直接出现在正文
        without_scoring = re.sub(r'<scoring>.*?</scoring>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()

        # 优先使用 <execution_content> 的内容
        execution_matches = re.findall(
            r'<execution_content[^>]*>(.*?)</execution_content>',
            without_scoring,
            flags=re.DOTALL | re.IGNORECASE,
        )
        for block in execution_matches:
            candidate = block.strip()
            if candidate:
                return candidate

        # 若存在 response_logic 块，整体去掉后再看是否有正文残留
        without_logic = re.sub(
            r'<response_logic[^>]*>.*?</response_logic>',
            '',
            without_scoring,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if without_logic:
            return without_logic

        # 兼容模型未包裹 response_logic、但直接输出内部块的情况
        without_internal_blocks = re.sub(
            r'<(?:planning|clarification_needed|vision_analysis)[^>]*>.*?</(?:planning|clarification_needed|vision_analysis)>',
            '',
            without_scoring,
            flags=re.DOTALL | re.IGNORECASE,
        )
        without_internal_tags = re.sub(
            r'</?(?:planning|clarification_needed|vision_analysis|execution_content|response_logic)[^>]*>',
            '',
            without_internal_blocks,
            flags=re.IGNORECASE,
        ).strip()
        if without_internal_tags:
            return without_internal_tags

        return without_scoring

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

    def _extract_progress_hint(self, content: str) -> Optional[str]:
        """提取模型在工具调用前输出的专用进度说明标签。"""
        match = re.search(
            r"<progress_hint[^>]*>(.*?)</progress_hint>",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return None

        hint = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if not hint:
            return None

        hint = hint.replace("\\n", "\n").strip()
        return hint or None

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], raw_content: str = "") -> AgentResponse:
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

        # 搜索开始前发送“过程说明 + 搜索词”
        if search_queries and self.registry.get_send_hook():
            progress_hint = self._extract_progress_hint(raw_content) if raw_content else None
            start_lines: List[str] = []
            if progress_hint:
                start_lines.append(progress_hint)
            start_lines.append("🔍 正在搜索:")
            for i, query in enumerate(search_queries, start=1):
                start_lines.append(f"  {i}. {query}")
            await self.registry.get_send_hook()("\n".join(start_lines))

        results: List[Any] = []
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理 registry 结果，添加到 api_messages
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
        logger.info(f"[Temperature] 更改成功: 会话温度已设置为 {self.config.temperature:.2f}")
        return AgentSession(
            self.client,
            self.model,
            user_input,
            registry or ToolRegistry(),
            images,
            temperature=self.config.temperature,
        )

    async def close(self):
        await self.client.close()
