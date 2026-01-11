"""
Vue-based Card Renderer (Minimal Python)

Python only provides raw data. All frontend logic (markdown, syntax highlighting,
math rendering, citations) is handled by the Vue frontend.
"""

import json
import gc
import os
import threading
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import Future

from loguru import logger
from playwright.async_api import async_playwright


class ContentRenderer:
    """Minimal renderer with background browser thread for instant startup."""
    
    def __init__(self, template_path: str = None, auto_start: bool = True):
        if template_path is None:
            current_dir = Path(__file__).parent
            template_path = current_dir / "assets" / "card-dist" / "index.html"
        
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Vue template not found: {self.template_path}")
            
        self.template_content = self.template_path.read_text(encoding="utf-8")
        logger.info(f"ContentRenderer: loaded Vue template ({len(self.template_content)} bytes)")
        
        # Browser state (managed by background thread)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._render_count = 0
        self._max_renders_before_restart = 50
        
        # Background event loop for playwright
        self._loop: asyncio.AbstractEventLoop = None
        self._thread: threading.Thread = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        
        if auto_start:
            self._start_background_loop()

    def _start_background_loop(self):
        """Start dedicated event loop in background thread."""
        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # Start browser immediately
            self._loop.run_until_complete(self._init_browser())
            self._ready.set()
            # Keep loop running for future tasks
            self._loop.run_forever()
        
        self._thread = threading.Thread(target=_run_loop, daemon=True, name="ContentRenderer-Browser")
        self._thread.start()
        logger.info("ContentRenderer: Background browser thread started")

    async def _init_browser(self, timeout: int = 6000):
        """Initialize browser and page with warmup render (runs in background loop)."""
        logger.info("ContentRenderer: Starting browser...")
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True, 
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            self._context = await self._browser.new_context(
                viewport={"width": 540, "height": 1400},
                device_scale_factor=2.0,
            )
            self._page = await self._context.new_page()
            await self._page.goto(self.template_path.as_uri(), wait_until="domcontentloaded", timeout=timeout)
            
            # Pre-warm the page with initial data so Vue compiles and renders
            warmup_data = {
                "markdown": "# Ready",
                "total_time": 0,
                "stages": [],
                "references": [],
                "page_references": [],
                "image_references": [],
                "stats": {},
                "theme_color": "#ef4444",
            }
            await self._page.evaluate("(data) => window.updateRenderData(data)", warmup_data)
            # await asyncio.sleep(0.1)  # Removed as requested
            logger.success("ContentRenderer: Browser + page ready!")
        except Exception as e:
            logger.error(f"ContentRenderer: Failed to start browser: {e}")
            raise

    def _run_in_background(self, coro) -> Future:
        """Schedule coroutine in background loop and return Future."""
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("Background loop not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def start(self, timeout: int = 6000):
        """Wait for browser to be ready (for compatibility)."""
        ready = await asyncio.to_thread(self._ready.wait, timeout / 1000)
        if not ready:
            raise TimeoutError("Browser startup timeout")

    async def close(self):
        """Clean up browser resources."""
        if self._loop and self._loop.is_running():
            future = self._run_in_background(self._close_internal())
            # Use asyncio.to_thread to wait without blocking the event loop
            await asyncio.to_thread(future.result, 10)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            # Use asyncio.to_thread to wait without blocking the event loop
            await asyncio.to_thread(self._thread.join, 5)
        logger.info("ContentRenderer: Browser closed.")

    async def _close_internal(self):
        """Internal close (runs in background loop)."""
        if self._page:
            await self._page.close()
            self._page = None
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure_page(self):
        """Ensure page is ready, restart if needed (runs in background loop)."""
        if self._render_count >= self._max_renders_before_restart:
            logger.info(f"ContentRenderer: Restarting browser after {self._render_count} renders...")
            await self._close_internal()
            self._render_count = 0
        
        if not self._page:
            await self._init_browser()

    async def render(
        self,
        markdown_content: str,
        output_path: str,
        stats: Dict[str, Any] = None,
        references: List[Dict[str, Any]] = None,
        page_references: List[Dict[str, Any]] = None,
        image_references: List[Dict[str, Any]] = None,
        stages_used: List[Dict[str, Any]] = None,
        image_timeout: int = 3000,
        theme_color: str = "#ef4444",
        **kwargs
    ) -> bool:
        """Render content to image."""
        # Wait for browser ready (non-blocking)
        ready = await asyncio.to_thread(self._ready.wait, 30)
        if not ready:
            logger.error("ContentRenderer: Browser not ready after 30s")
            return False
        
        # Prepare data
        resolved_output_path = Path(output_path).resolve()
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        
        stats_dict = stats[0] if isinstance(stats, list) and stats else (stats or {})
        
        render_data = {
            "markdown": markdown_content,
            "total_time": stats_dict.get("total_time", 0) or 0,
            "stages": [
                {
                    "name": s.get("name", "Step"),
                    "model": s.get("model", ""),
                    "provider": s.get("provider", ""),
                    "time": s.get("time", 0),
                    "cost": s.get("cost", 0),
                    "references": s.get("references") or s.get("search_results"),
                    "image_references": s.get("image_references"),
                    "crawled_pages": s.get("crawled_pages"),
                }
                for s in (stages_used or [])
            ],
            "references": references or [],
            "page_references": page_references or [],
            "image_references": image_references or [],
            "stats": stats_dict,
            "theme_color": theme_color,
        }
        
        # Reorder images in stages
        self._reorder_images_in_stages(render_data["markdown"], render_data["stages"])
        
        # Run render in background loop (non-blocking wait for result)
        try:
            future = self._run_in_background(
                self._render_internal(render_data, str(resolved_output_path), image_timeout)
            )
            # Use asyncio.to_thread to wait for the future without blocking the event loop
            return await asyncio.to_thread(future.result, 60)
        except Exception as e:
            logger.error(f"ContentRenderer: render failed ({e})")
            return False

    async def _render_internal(self, render_data: dict, output_path: str, image_timeout: int) -> bool:
        """Internal render (runs in background loop)."""
        import time
        start_time = time.time()
        
        try:
            await self._ensure_page()
            
            # Update data via JS
            await self._page.evaluate("(data) => window.updateRenderData(data)", render_data)
            
            # Wait for Vue to update DOM
            # await asyncio.sleep(0.1) # Removed as requested
            
            # Wait for images to load
            try:
                await self._page.wait_for_function(
                    "() => Array.from(document.images).every(img => img.complete)",
                    timeout=image_timeout
                )
            except Exception:
                logger.warning(f"ContentRenderer: Timeout waiting for images ({image_timeout}ms)")
            
            # Take screenshot
            element = await self._page.query_selector("#main-container")
            if element:
                await element.screenshot(path=output_path, type="jpeg", quality=88)
            else:
                await self._page.screenshot(path=output_path, full_page=True, type="jpeg", quality=88)
            
            self._render_count += 1
            duration = time.time() - start_time
            logger.success(f"ContentRenderer: Rendered in {duration:.3f}s (No.{self._render_count})")
            return True
            
        except Exception as exc:
            logger.error(f"ContentRenderer: render failed ({exc})")
            # Reset page to force restart next time
            self._page = None
            return False
        finally:
            gc.collect()

    async def render_models_list(
        self,
        models: List[Dict[str, Any]],
        output_path: str,
        default_base_url: str = "https://openrouter.ai/api/v1",
        **kwargs
    ) -> bool:
        """Render models list."""
        lines = ["# 模型列表"]
        for idx, model in enumerate(models or [], start=1):
            name = model.get("name", "unknown")
            base_url = model.get("base_url") or default_base_url
            provider = model.get("provider", "")
            lines.append(f"{idx}. **{name}**  \n   - base_url: {base_url}  \n   - provider: {provider}")

        markdown_content = "\n\n".join(lines) if len(lines) > 1 else "# 模型列表\n暂无模型"

        return await self.render(
            markdown_content=markdown_content,
            output_path=output_path,
            stats={},
            references=[],
            stages_used=[],
        )

    def _reorder_images_in_stages(self, markdown: str, stages: List[Dict[str, Any]]) -> None:
        """Reorder image references in stages based on appearance in markdown."""
        import re
        
        img_urls = []
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', markdown):
            url_part = match.group(1).split()[0].strip()
            if url_part and url_part not in img_urls:
                img_urls.append(url_part)
                
        if not img_urls:
            return

        for stage in stages:
            refs = stage.get("image_references")
            if not refs:
                continue
                
            ref_map = {r["url"]: r for r in refs}
            new_refs = []
            seen_urls = set()
            
            for url in img_urls:
                if url in ref_map:
                    new_refs.append(ref_map[url])
                    seen_urls.add(url)
            
            for r in refs:
                if r["url"] not in seen_urls:
                    new_refs.append(r)
            
            stage["image_references"] = new_refs
