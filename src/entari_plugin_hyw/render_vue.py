"""
Vue-based Card Renderer (DrissionPage-based)

Renders content to image using the shared DrissionPage browser.
Wraps synchronous DrissionPage operations in a thread pool.
"""

import json
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from loguru import logger
from .browser.manager import SharedBrowserManager


class ContentRenderer:
    """Renderer using DrissionPage with thread pool for async interface."""
    
    def __init__(self, template_path: str = None, auto_start: bool = True, headless: bool = True):
        self.headless = headless
        
        if template_path is None:
            current_dir = Path(__file__).parent
            template_path = current_dir / "assets" / "card-dist" / "index.html"
        
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Vue template not found: {self.template_path}")
            
        self.template_content = self.template_path.read_text(encoding="utf-8")
        logger.info(f"ContentRenderer: loaded Vue template ({len(self.template_content)} bytes)")
        
        self._manager = None
        self._executor = ThreadPoolExecutor(max_workers=10) # Enough for batch crawls
        self._render_tab = None
        
        if auto_start:
            self._ensure_manager()

    def _ensure_manager(self):
        """Ensure shared browser manager exists."""
        if not self._manager:
            from .browser.manager import get_shared_browser_manager
            self._manager = get_shared_browser_manager(headless=self.headless)

    async def start(self, timeout: int = 6000):
        """Initialize renderer manager (async wrapper)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._ensure_manager)
    
    async def prepare_tab(self) -> str:
        """Async wrapper to prepare a new render tab."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._prepare_tab_sync)

    def _prepare_tab_sync(self) -> str:
        """Create and warm up a new tab, return its ID."""
        import time as pytimeout
        start = pytimeout.time()
        self._ensure_manager()
        try:
            tab = self._manager.new_tab(self.template_path.as_uri())
            tab_id = tab.tab_id
            
            # Basic wait
            tab.wait(1)
            
            # Pre-warm
            warmup_data = {
                "markdown": "# Ready",
                "total_time": 0,
                "stages": [],
                "references": [],
                "stats": {},
                "theme_color": "#ef4444",
            }
            
            if tab.ele('#app', timeout=5):
                tab.run_js(f"window.updateRenderData({json.dumps(warmup_data)})")
            
            elapsed = pytimeout.time() - start
            logger.info(f"ContentRenderer: Prepared tab {tab_id} in {elapsed:.2f}s")
            return tab_id
        except Exception as e:
            logger.error(f"ContentRenderer: Failed to prepare tab: {e}")
            raise

    async def render_pages_batch(
        self,
        pages: List[Dict[str, Any]],
        theme_color: str = "#ef4444"
    ) -> List[str]:
        """
        Render multiple page markdown contents to images concurrently.
        
        Args:
            pages: List of dicts with 'title', 'content', 'url' keys
            theme_color: Theme color for rendering
            
        Returns:
            List of base64-encoded JPG images
        """
        if not pages:
            return []
        
        loop = asyncio.get_running_loop()
        
        # Prepare tabs concurrently
        logger.info(f"ContentRenderer: Preparing {len(pages)} tabs for batch render")
        tab_tasks = [
            loop.run_in_executor(self._executor, self._prepare_tab_sync)
            for _ in pages
        ]
        tab_ids = await asyncio.gather(*tab_tasks, return_exceptions=True)
        
        # Filter out failed tab preparations
        valid_pairs = []
        for i, (page, tab_id) in enumerate(zip(pages, tab_ids)):
            if isinstance(tab_id, Exception):
                logger.warning(f"ContentRenderer: Failed to prepare tab for page {i}: {tab_id}")
            else:
                valid_pairs.append((page, tab_id))
        
        if not valid_pairs:
            return []
        
        # Render concurrently
        render_tasks = [
            loop.run_in_executor(
                self._executor, 
                self._render_page_to_b64_sync,
                page,
                tab_id,
                theme_color
            )
            for page, tab_id in valid_pairs
        ]
        
        results = await asyncio.gather(*render_tasks, return_exceptions=True)
        
        # Process results
        screenshots = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning(f"ContentRenderer: Batch render error for page {i}: {res}")
                screenshots.append(None)
            else:
                screenshots.append(res)
        
        logger.info(f"ContentRenderer: Batch rendered {len([s for s in screenshots if s])} pages")
        return screenshots

    def _render_page_to_b64_sync(
        self,
        page_data: Dict[str, Any],
        tab_id: str,
        theme_color: str
    ) -> Optional[str]:
        """Render a single page's markdown to base64 image."""
        tab = None
        try:
            self._ensure_manager()
            browser_page = self._manager.page
            
            try:
                tab = browser_page.get_tab(tab_id)
            except Exception:
                return None
            
            if not tab:
                return None
            
            # Build render data for this page
            markdown = f"# {page_data.get('title', 'Page')}\n\n{page_data.get('content', '')}"
            
            render_data = {
                "markdown": markdown,
                "total_time": 0,
                "stages": [],
                "references": [],
                "page_references": [],
                "image_references": [],
                "stats": {},
                "theme_color": theme_color,
            }
            
            # 1. Update Data & Settle
            tab.run_js(f"window.updateRenderData({json.dumps(render_data)})")
            tab.wait(0.5) # Since images are Base64, decoding is nearly instant once injected

            # 2. Dynamic Resize
            # Get actual content height to prevent clipping
            scroll_height = tab.run_js('return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);')
            viewport_height = int(scroll_height) + 200
            
            tab.run_cdp('Emulation.setDeviceMetricsOverride', 
                width=1920, height=viewport_height, deviceScaleFactor=1, mobile=False
            )
            
            # 3. Hide Scrollbars (Now that viewport is large enough, overflow:hidden won't clip)
            tab.run_js('document.documentElement.style.overflow = "hidden"')
            tab.run_js('document.body.style.overflow = "hidden"')
            
            # Use element's actual position and size
            main_ele = tab.ele('#main-container', timeout=3)
            if main_ele:
                # Robustly hide scrollbars via CDP and Style Injection
                SharedBrowserManager.hide_scrollbars(tab)
                
                # Force root styles to eliminate gutter and ensure full width
                tab.run_js('document.documentElement.style.overflow = "hidden";')
                tab.run_js('document.body.style.overflow = "hidden";')
                tab.run_js('document.documentElement.style.scrollbarGutter = "unset";') 
                tab.run_js('document.documentElement.style.width = "100%";')

                orig_overflow = "auto" # just a placeholder, we rely on full refresh usually or don't care about restoring for single-purpose tabs
                
                b64_img = main_ele.get_screenshot(as_base64='jpg')
                
                # Restore not strictly needed for throwaway render tabs, but good practice
                # tab.run_js(f'document.documentElement.style.overflow = "{orig_overflow}";')
                try:
                    tab.set.scroll_bars(True)
                except:
                    pass
                return b64_img
            else:
                return tab.get_screenshot(as_base64='jpg', full_page=False)
                
        except Exception as e:
            logger.error(f"ContentRenderer: Failed to render page: {e}")
            return None
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:
                    pass


    async def render(
        self,
        markdown_content: str,
        output_path: str,
        tab_id: Optional[str] = None,
        stats: Dict[str, Any] = None,
        references: List[Dict[str, Any]] = None,
        page_references: List[Dict[str, Any]] = None,
        image_references: List[Dict[str, Any]] = None,
        stages_used: List[Dict[str, Any]] = None,
        theme_color: str = "#ef4444",
        **kwargs
    ) -> bool:
        """Render content to image using a specific (pre-warmed) tab or a temp one."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._render_sync,
            markdown_content,
            output_path,
            tab_id,
            stats,
            references,
            page_references,
            image_references,
            stages_used,
            theme_color
        )

    def _render_sync(
        self,
        markdown_content: str,
        output_path: str,
        tab_id: Optional[str],
        stats: Dict[str, Any],
        references: List[Dict[str, Any]],
        page_references: List[Dict[str, Any]],
        image_references: List[Dict[str, Any]],
        stages_used: List[Dict[str, Any]],
        theme_color: str
    ) -> bool:
        """Synchronous render implementation."""
        tab = None
        
        try:
            self._ensure_manager()
            page = self._manager.page
            
            if tab_id:
                try:
                    tab = page.get_tab(tab_id)
                except Exception:
                    pass
            
            if not tab:
                logger.warning("ContentRenderer: Pre-warmed tab not found, creating new.")
                tab = page.new_tab(self.template_path.as_uri())
                tab.wait(0.5)
            
            resolved_output_path = Path(output_path).resolve()
            resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
            
            stats_dict = stats[0] if isinstance(stats, list) and stats else (stats or {})
            
            render_data = {
                "markdown": markdown_content,
                "total_time": stats_dict.get("total_time", 0) or 0,
                "stages": stages_used or [],
                "references": references or [],
                "page_references": page_references or [],
                "image_references": image_references or [],
                "stats": stats_dict,
                "theme_color": theme_color,
            }
            
            tab.run_js(f"window.updateRenderData({json.dumps(render_data)})")

            # Brief settle wait for masonry/images
            tab.wait(0.6)
            
            # Dynamic Resize
            scroll_height = tab.run_js('return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);')
            viewport_height = int(scroll_height) + 200
            
            tab.run_cdp('Emulation.setDeviceMetricsOverride', 
                width=1920, height=viewport_height, deviceScaleFactor=1, mobile=False
            )
            
            # Hide scrollbars
            tab.run_js('document.documentElement.style.overflow = "hidden"')
            tab.run_js('document.body.style.overflow = "hidden"')
            
            # Use element's actual position and size
            main_ele = tab.ele('#main-container', timeout=5)
            if main_ele:
                import base64
                
                # Robustly hide scrollbars via CDP and Style Injection
                SharedBrowserManager.hide_scrollbars(tab)
                
                # Force root styles to eliminate gutter and ensure full width
                tab.run_js('document.documentElement.style.overflow = "hidden";')
                tab.run_js('document.body.style.overflow = "hidden";')
                tab.run_js('document.documentElement.style.scrollbarGutter = "unset";') 
                tab.run_js('document.documentElement.style.width = "100%";')

                b64_img = main_ele.get_screenshot(as_base64='jpg')

                # Restore scrollbars (optional here since we often close or navigate away)
                try:
                    tab.set.scroll_bars(True)
                except:
                    pass

                with open(str(resolved_output_path), 'wb') as f:
                    f.write(base64.b64decode(b64_img))
            else:
                logger.warning("ContentRenderer: #main-container not found, using fallback")
                tab.get_screenshot(path=str(resolved_output_path.parent), name=resolved_output_path.name, full_page=True)
            
            return True
        except Exception as e:
            logger.error(f"ContentRenderer: Render failed: {e}")
            return False
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:
                    pass

    async def close(self):
        """Close renderer."""
        self._executor.shutdown(wait=False)
        if self._render_tab:
            try:
                self._render_tab.close()
            except Exception:
                pass
            self._render_tab = None


# Singleton
_content_renderer: Optional[ContentRenderer] = None



async def get_content_renderer() -> ContentRenderer:
    global _content_renderer
    if _content_renderer is None:
        _content_renderer = ContentRenderer()
        await _content_renderer.start()
    return _content_renderer


def set_global_renderer(renderer: ContentRenderer):
    """Set the global renderer instance."""
    global _content_renderer
    _content_renderer = renderer
