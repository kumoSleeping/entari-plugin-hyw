from typing import List
from .registry import ToolRegistry
from ..config import HywCoreConfig
from ..processor import ResponseProcessor

# === 显式导入所有工具 (函数式) ===
from .web_search import web_search
from .web_fetch import web_fetch

# === 渲染功能 (不再作为 LLM 工具，由插件层根据结构化输出调用) ===
# 使用方法:
#   from entari_plugin_hyw.core.tools.render import render
#   result = await render(content, title, headless=True)
#   # result 是 ToolResult，result.content 包含 base64 图片

def load_tools(config: HywCoreConfig) -> ToolRegistry:
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
    async def _search(query: str, kl: str = "", time_range: str = ""):
        return await web_search(query, kl=kl, time_range=time_range, headless=config.headless)
    registry.register("web_search", _search)

    # 2. Web Fetch (Replaces Screenshot)
    async def _web_fetch(url: str):
        return await web_fetch(url, headless=config.headless)
    registry.register("web_fetch", _web_fetch)

    return registry

def load_processors() -> List[ResponseProcessor]:
    """手动加载所有响应处理器"""
    # render_processor 也可移除，因为渲染逻辑改为显式调用
    return []
