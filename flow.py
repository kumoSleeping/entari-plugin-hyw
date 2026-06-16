from typing import Callable, Awaitable, Optional, List, Dict, Union, Any
from dataclasses import dataclass
from .agent import HywAgent, AgentSession
from .models import Message
from .tool_registry import ToolRegistry

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
        max_turns: int = 15,
        search_turn_budget: int = 10,
        finalization_turns: int = 5,
        images: Optional[List[str]] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        start_turn: int = 0,
        continuation: bool = False
    ) -> FlowResult:
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

        max_turns = max(max_turns, search_turn_budget + finalization_turns)
        last_response = None
        while not session.finished and session.turn < max_turns:
            if policy_func:
                if await policy_func(session) is False:
                    break

            if session.turn == 6:
                session.configure(
                    temp_prompt=(
                        "你已经进行了多轮搜索。现在请判断是否真的还有新的信息增量："
                        "如果只是在围绕同一个实体、同一组关键词或同一种说法反复展开，"
                        "通常已经没有什么新的东西值得继续搜索了。\n"
                        "接下来优先整合已有证据并收束答案；只有当你能明确提出一个全新的、"
                        "能改变结论的检索角度时，才继续搜索。"
                    ),
                )

            if session.turn >= search_turn_budget:
                remaining = max_turns - session.turn
                stop_within = min(3, remaining)
                session.configure(
                    tools=[],
                    final_only=True,
                    temp_prompt=(
                        "搜索轮次已经用完，现在必须进入最终收束阶段。\n"
                        "本轮工具已经不可用，禁止输出 <tool_call>、<progress_hint> 或新的检索计划。\n"
                        "只允许输出一个 <final_response>...</final_response>，标签内直接写最终答案正文。\n"
                        f"你必须在 {stop_within} 轮以内停止并给出可用结果；"
                        "如果证据不足，也要简短说明已知信息、未确认点和无法确定的原因。"
                    ),
                )

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
