from .agent import HywAgent, AgentSession
from .models import Message, AgentResponse
from .flow import FlowRunner, Flow, FlowResult
from .policies import chat_flow
from .tools.registry import ToolRegistry
from .tools.base import ToolDef
from .tools.loader import load_tools, load_processors
from .processor import apply_processors, ProcessedResponse
from .tools._public.browser import prestart_browser
from .tools._public.browser.renderer import get_content_renderer
from .config import HywCoreConfig

async def warmup(headless: bool = True):
    """预热核心服务 (浏览器 + 渲染器)"""
    # 1. 启动浏览器进程 (Sync)
    prestart_browser(headless=headless)
    # 2. 预初始化渲染器 (Async) - 加载 Vue 模板
    await get_content_renderer(headless=headless)

__all__ = [
    "HywAgent", "AgentSession", "Message", "AgentResponse",
    "FlowRunner", "Flow", "FlowResult", "chat_flow",
    "ToolRegistry", "ToolDef", "load_tools", "load_processors",
    "apply_processors", "ProcessedResponse",
    "HywCoreConfig", "warmup"
]
