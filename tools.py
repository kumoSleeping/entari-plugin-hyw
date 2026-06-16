from .tool_registry import ToolRegistry
from .config import HywConfigData

# === 显式导入所有工具 (函数式) ===
from .browser_action import browser_action

def load_tools(config: HywConfigData) -> ToolRegistry:
    """
    手动加载并初始化所有核心工具。

    注意: render 已从工具列表中移除，改为结构化输出控制。
    插件层根据 AgentResponse.should_render 决定是否调用渲染。

    send_hook 通过 registry.set_send_hook() 动态设置，示例:
        registry.set_send_hook(session.send)

    搜索通知已移至 agent._execute_tools() 统一汇总发送。
    """
    registry = ToolRegistry()

    async def _browser_action(
        action: str,
        url: str = "",
        query: str = "",
        selector: str = "",
        ref: str = "",
        tab_id: str = "",
        text: str = "",
        key: str = "",
        target: str = "",
        timeout: float = 10.0,
    ):
        return await browser_action(
            action=action,
            url=url,
            query=query,
            selector=selector,
            ref=ref,
            tab_id=tab_id,
            text=text,
            key=key,
            target=target,
            timeout=timeout,
            headless=config.headless,
        )
    registry.register("browser_action", _browser_action)

    return registry

async def warmup(headless: bool = True):
    """Warm up browser and renderer."""
    import asyncio
    from .browser import prestart_browser
    from .browser.renderer import get_content_renderer

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, prestart_browser, headless)
    await get_content_renderer(headless=headless)
