from typing import Callable, Awaitable, Optional, List, Dict, Union, Any
from dataclasses import dataclass
from .agent import HywAgent, AgentSession
from .models import Message
from .tool_registry import ToolRegistry
from .search_jina import reset_search_index

# 基础策略类型: 返回 None 继续, 返回 False 停止
FlowPolicy = Callable[[AgentSession], Awaitable[Union[None, bool]]]

# Prompt 类型：字符串 或 接收Session返回字符串的函数
PromptType = Union[str, Callable[[AgentSession], Optional[str]]]


@dataclass
class FlowResult:
    """Flow 执行结果"""
    history: List[Message]
    content: str = ""
    should_render: bool = False
    is_final: bool = True
    scoring: Optional[List[Dict]] = None  # 评分信息

class Flow:
    """
    流程定义器
    """
    def __init__(self):
        self._turn_configs: Dict[int, Dict] = {}
        self._every_turn_config: Dict = {}
        self._registry: Optional[ToolRegistry] = None

    def set_registry(self, registry: ToolRegistry):
        """设置全局工具注册表"""
        self._registry = registry
        return self

    def every_turn(self, prompt: Optional[PromptType] = None):
        self._every_turn_config = {"prompt": prompt}
        return self

    def at_turn(self,
                turn: int,
                prompt: Optional[PromptType] = None,
                tools: Optional[List[Dict]] = None,
                tool_choice: Optional[Union[str, Dict]] = None,
                clean_history: bool = False):
        self._turn_configs[turn] = {
            "prompt": prompt,
            "tools": tools,
            "tool_choice": tool_choice,
            "clean_history": clean_history
        }
        return self

    def _resolve_prompt(self, prompt_def: Optional[PromptType], session: AgentSession) -> Optional[str]:
        if prompt_def is None:
            return None
        if callable(prompt_def):
            return prompt_def(session)
        return str(prompt_def)

    async def __call__(self, session: AgentSession):
        next_turn = session.turn + 1

        # 1. 应用全局轮次配置
        if self._every_turn_config:
            p = self._resolve_prompt(self._every_turn_config.get("prompt"), session)
            if p:
                session.configure(prompt=p)

        # 2. 应用特定轮次配置
        config = self._turn_configs.get(next_turn)
        if config:
            p = self._resolve_prompt(config["prompt"], session)

            if config.get("prompt") or config.get("tool_choice"):
                print(f"  [Flow] 应用第 {next_turn} 轮配置...")

            session.configure(
                temp_prompt=p,
                tools=config["tools"],
            )

class FlowRunner:
    def __init__(self, agent: HywAgent, registry: Optional[ToolRegistry] = None):
        self.agent = agent
        self.registry = registry

    async def run(
        self,
        user_input: str,
        flow: Union[Flow, FlowPolicy, None] = None,
        max_turns: int = 10,
        images: Optional[List[str]] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        start_turn: int = 0,
        continuation: bool = False
    ) -> FlowResult:
        # 重置搜索序号计数器，确保每次新对话从 1 开始
        reset_search_index()

        target_registry = self.registry
        policy_func = None

        if isinstance(flow, Flow):
            if flow._registry:
                target_registry = flow._registry
            policy_func = flow
        elif callable(flow):
            policy_func = flow

        session = self.agent.init_session(user_input, target_registry, images)
        if start_turn:
            session.turn = start_turn
        if continuation:
            session.is_continuation = True

        # 预置历史上下文（仅拼接为消息，不触发系统提示）
        if history_messages:
            history_api = []
            history_internal = []
            for msg in history_messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content", "")
                if role not in ("user", "assistant", "system"):
                    continue
                history_api.append({"role": role, "content": content})
                history_internal.append(Message(role=role, content=str(content)))

            if history_api:
                session.api_messages = history_api + session.api_messages
                session.history = history_internal + session.history
        self._session = session

        last_response = None
        while not session.finished and session.turn < max_turns:
            if policy_func:
                if await policy_func(session) is False:
                    break

            last_response = await session.step()

            if not last_response.success:
                print(f"Flow Error: {last_response.error}")
                break

        # 返回完整结果
        return FlowResult(
            history=session.history,
            content=last_response.content if last_response else "",
            should_render=last_response.should_render if last_response else False,
            is_final=last_response.is_final if last_response else True,
            scoring=getattr(last_response, 'scoring', None) if last_response else None
        )
