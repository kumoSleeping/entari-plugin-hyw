from .tool_registry import ToolRegistry
from .config import HywConfigData

# === 显式导入所有工具 (函数式) ===
from .search_jina import web_search
from .fetch_browser import web_fetch

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

    # 1. Web Search
    async def _search(query: str):
        return await web_search(query, headless=config.headless)
    registry.register("web_search", _search)

    # 2. Web Fetch (Replaces Screenshot)
    async def _web_fetch(url: str):
        return await web_fetch(url, headless=config.headless)
    registry.register("web_fetch", _web_fetch)

    return registry

async def warmup(headless: bool = True):
    """Warm up browser and renderer."""
    import asyncio
    from .browser import prestart_browser
    from .browser.renderer import get_content_renderer

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, prestart_browser, headless)
    await get_content_renderer(headless=headless)
