import asyncio
import json
import re
from loguru import logger
from typing import List, Optional, Dict, Any, Union
from .llm import HttpChatClient
from .models import Message, AgentResponse, ToolResult
from .tool_registry import ToolRegistry
from .config import HywConfigData
from .xml_protocol import (
    extract_final_content,
    extract_progress_hint,
    extract_scoring,
    extract_tag_content,
    extract_tool_calls,
    parse_tool_params,
)

class AgentSession:
    """Agent 会话，使用 XML 格式输出，自行解析"""

    def __init__(
        self,
        client: HttpChatClient,
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
        self.final_format_retry_count = 0
        self.max_final_format_retries = 2
        self.format_retry_pending = False

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
            response = await self.client.create_chat_completion(**kwargs)

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

        # 3. 最终回复必须使用 <final_response> 包裹
        final_content_raw = self._extract_final_response(raw_content)
        if final_content_raw is None:
            fallback_content = self._coerce_unwrapped_final_response(raw_content)
            if fallback_content:
                self.history.append(Message(role="assistant", content=fallback_content))
                self.finished = True
                return AgentResponse(
                    content=fallback_content,
                    history=self.history,
                    is_final=True,
                    should_render=self._has_markdown(fallback_content),
                    scoring=scoring,
                )
            return self._request_final_format_retry(
                "最终回复缺少 <final_response>...</final_response> 包裹。"
            )
        final_content = self._extract_final_content(final_content_raw)
        if not final_content:
            final_content = final_content_raw
        final_content = self._remove_clickable_links(final_content)

        if self._contains_forbidden_process_phrasing(final_content):
            return self._request_final_format_retry(
                "最终回复包含流程性话术，请直接给出结果。"
            )

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

    def _extract_final_response(self, content: str) -> Optional[str]:
        """提取最终回复标签 <final_response>...</final_response>。"""
        return extract_tag_content(content, "final_response")

    def _contains_forbidden_process_phrasing(self, text: str) -> bool:
        """检查是否包含不应出现在最终答案中的流程性话术。"""
        normalized = re.sub(r"\s+", "", text)
        forbidden = [
            "基于已获取的高分搜索结果",
            "无需进一步搜索",
            "直接进行结构化总结回复",
            "基于搜索到",
            "直接给出结论",
        ]
        return any(p.replace(" ", "") in normalized for p in forbidden)

    def _coerce_unwrapped_final_response(self, raw_content: str) -> Optional[str]:
        """Accept obvious final prose when the model forgot the XML wrapper."""
        if not raw_content:
            return None
        if re.search(r"<(?:tool_call|response_logic|planning|clarification_needed|vision_analysis|execution_content)\b", raw_content, re.IGNORECASE):
            return None

        cleaned = self._remove_clickable_links(extract_final_content(raw_content))
        if not cleaned or self._contains_forbidden_process_phrasing(cleaned):
            return None

        if self._has_markdown(cleaned) or len(cleaned.strip()) > 80:
            logger.info("[Format] 接受未包裹的最终正文并按最终回复处理。")
            return cleaned
        return None

    def _remove_clickable_links(self, text: str) -> str:
        """移除最终正文中的可点击链接，保留可读文本。"""
        if not text:
            return ""
        without_markdown_links = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        without_markdown_links = re.sub(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]+\)", r"\1", without_markdown_links)
        without_angle_links = re.sub(r"<(?:https?://|www\.)[^>\s]+>", "", without_markdown_links)
        without_bare_urls = re.sub(r"\b(?:https?://|www\.)\S+", "", without_angle_links)
        return re.sub(r"[ \t]{2,}", " ", without_bare_urls).strip()

    def _request_final_format_retry(self, reason: str) -> AgentResponse:
        """最终回复不满足期望时，不结束会话，继续引导模型完成。"""
        logger.info(f"[Format] 继续运行，等待最终回复: {reason}")
        self.final_format_retry_count += 1
        if self.final_format_retry_count > self.max_final_format_retries:
            self.finished = True
            return AgentResponse(
                content="我这次没有拿到可用的最终回复格式，先停止，避免继续空转。",
                history=self.history,
                is_final=True,
                should_render=False,
            )
        self.format_retry_pending = True
        self.api_messages.append(
            {
                "role": "user",
                "content": (
                    "请继续完成当前任务。\n"
                    "若还需要工具，请先调用工具；\n"
                    "若可直接回答，请仅输出 <final_response>...</final_response> 并给出结果，避免流程话术。"
                ),
            }
        )
        return AgentResponse(content="", history=self.history, success=True)

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
        return extract_scoring(content)

    def _extract_final_content(self, content: str) -> str:
        """提取最终可展示内容，过滤 response_logic 内部结构。"""
        return extract_final_content(content)

    def _extract_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """从内容中提取所有工具调用"""
        return extract_tool_calls(content, self.available_tools)

    def _parse_tool_params(self, params_xml: str) -> Dict[str, Any]:
        """解析工具参数 XML，如 <query>xxx</query><url>yyy</url>"""
        return parse_tool_params(params_xml)

    def _extract_progress_hint(self, content: str) -> Optional[str]:
        """提取模型在工具调用前输出的专用进度说明标签。"""
        return extract_progress_hint(content)

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], raw_content: str = "") -> AgentResponse:
        """执行从结构化输出解析出的工具调用"""
        tasks, infos = [], []
        search_queries: List[Dict[str, str]] = []

        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]

            # 收集搜索查询
            if name == "web_search":
                args.pop("time_range", None)
                args.pop("kl", None)
                search_queries.append({
                    "query": args.get("query", ""),
                })

            tasks.append(self.registry.execute(name, args))
            infos.append(name)

        # 搜索开始前发送“过程说明 + 搜索词”
        if search_queries and self.registry.get_send_hook():
            progress_hint = self._extract_progress_hint(raw_content) if raw_content else None
            start_lines: List[str] = []
            if progress_hint:
                start_lines.append(progress_hint)
            start_lines.append("🔍 正在搜索:")
            for i, search_call in enumerate(search_queries, start=1):
                query = search_call.get("query", "")
                start_lines.append(f"  {i}. {query}")
            await self.registry.get_send_hook()("\n".join(start_lines))

        results: List[Any] = []
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

        search_result_index = 0

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

            if name == "web_search":
                try:
                    payload = json.loads(tool_content)
                    rows = payload.get("results", [])
                    if isinstance(rows, list):
                        for row in rows:
                            if isinstance(row, dict):
                                search_result_index += 1
                                row["index"] = search_result_index
                        tool_content = json.dumps(payload, ensure_ascii=False, indent=2)
                except Exception:
                    pass

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
    def __init__(self, config: HywConfigData):
        self.config = config
        self.client = HttpChatClient(api_key=config.api_key, base_url=config.base_url)
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
