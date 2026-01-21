"""
hyw_core.browser_control - Browser automation and rendering

This subpackage provides:
- BrowserManager: Shared browser instance management
- PageService: Page fetching and screenshot capabilities
- RenderService: Vue-based card rendering
- Search engines: Bing, Google, DuckDuckGo adapters
"""

from .manager import (
    SharedBrowserManager,
    get_shared_browser_manager,
    close_shared_browser,
)

from .service import (
    ScreenshotService,
    get_screenshot_service,
    close_screenshot_service,
    prestart_browser,
)

from .renderer import (
    ContentRenderer,
    get_content_renderer,
    set_global_renderer,
)

from .engines.base import SearchEngine
from .engines.google import GoogleEngine
from .engines.duckduckgo import DuckDuckGoEngine
from .engines.default import DefaultEngine

# Aliases for cleaner API
BrowserManager = SharedBrowserManager
PageService = ScreenshotService
RenderService = ContentRenderer

__all__ = [
    # Browser Management
    "BrowserManager",
    "SharedBrowserManager", 
    "get_shared_browser_manager",
    "close_shared_browser",
    
    # Page Service
    "PageService",
    "ScreenshotService",
    "get_screenshot_service",
    "close_screenshot_service",
    "prestart_browser",
    
    # Render Service
    "RenderService",
    "ContentRenderer",
    "get_content_renderer",
    "set_global_renderer",
    
    # Search Engines
    "SearchEngine",
    "GoogleEngine",
    "DuckDuckGoEngine",
    "DefaultEngine",
]
