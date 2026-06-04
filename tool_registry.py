from typing import Dict, Any, Callable, Awaitable, Optional

# Hook 类型
SendHook = Optional[Callable[[str], Awaitable[None]]]

class ToolRegistry:
    def __init__(self):
        # 存储: name -> async_function
        self._tools: Dict[str, Callable[..., Awaitable[Any]]] = {}
        # 动态发送 hook (可在每次请求时设置)
        self._send_hook: SendHook = None

    def register(self, name: str, func: Callable):
        self._tools[name] = func
        return self

    def set_send_hook(self, hook: SendHook):
        """设置发送消息的 hook，用于工具执行时发送通知"""
        self._send_hook = hook

    def get_send_hook(self) -> SendHook:
        """获取当前的发送 hook"""
        return self._send_hook

    async def execute(self, name: str, args: Dict[str, Any]) -> Any:
        print(f"[Registry DEBUG] Executing tool: {name}")
        func = self._tools.get(name)
        if not func:
            print(f"[Registry DEBUG] Tool '{name}' not found! Available: {list(self._tools.keys())}")
            # 如果找不到工具，返回错误信息给 LLM
            return f"Error: Tool '{name}' implementation not found."

        try:
            # 直接调用函数，传入解包的参数
            return await func(**args)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Error executing tool '{name}': {str(e)}"
