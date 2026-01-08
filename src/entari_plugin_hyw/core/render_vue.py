"""
Vue-based Card Renderer (Minimal Python)

Python only provides raw data. All frontend logic (markdown, syntax highlighting,
math rendering, citations) is handled by the Vue frontend.
"""

import json
import gc
import uuid
import os
from pathlib import Path
from typing import List, Dict, Any

import asyncio
from loguru import logger
from playwright.async_api import async_playwright


class ContentRenderer:
    """Minimal renderer - only passes raw data to Vue template."""
    
    
    def __init__(self, template_path: str = None):
        if template_path is None:
            current_dir = Path(__file__).parent
            plugin_root = current_dir.parent
            template_path = plugin_root / "assets" / "card-dist" / "index.html"
        
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Vue template not found: {self.template_path}")
            
        self.template_content = self.template_path.read_text(encoding="utf-8")
        logger.info(f"ContentRenderer: loaded Vue template ({len(self.template_content)} bytes)")
        
        # Persistent state
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._lock = asyncio.Lock()
        self._render_count = 0
        self._max_renders_before_restart = 50  # Prevent memory leaks

    async def start(self):
        """Initialize the browser and page."""
        if self.page:
            return

        logger.info("ContentRenderer: Starting persistent browser...")
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            self.context = await self.browser.new_context(
                viewport={"width": 520, "height": 1400},
                device_scale_factor=2.4,
            )
            self.page = await self.context.new_page()
            
            # Load the template once
            await self.page.goto(self.template_path.as_uri(), wait_until="networkidle")
            logger.info("ContentRenderer: Browser started and template loaded.")
            
        except Exception as e:
            logger.error(f"ContentRenderer: Failed to start browser: {e}")
            await self.close()
            raise

    async def close(self):
        """Clean up browser resources."""
        if self.page:
            await self.page.close()
            self.page = None
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("ContentRenderer: Browser closed.")

    async def _get_page(self):
        """Get or recreate the persistent page."""
        if self._render_count >= self._max_renders_before_restart:
            logger.info(f"ContentRenderer: Restarting browser after {self._render_count} renders...")
            await self.close()
            self._render_count = 0

        if not self.page:
            await self.start()
        
        return self.page

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
        **kwargs
    ) -> bool:
        """Render content to image using persistent browser."""
        
        resolved_output_path = Path(output_path).resolve()
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Prepare data
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
        }
        import time
        start_time = time.time()
        
        # Reorder images
        self._reorder_images_in_stages(render_data["markdown"], render_data["stages"])

        async with self._lock:
            try:
                page = await self._get_page()
                
                # Update data via JS
                # Using evaluate to call window.updateRenderData
                await page.evaluate("(data) => window.updateRenderData(data)", render_data)
                
                
                # Wait for Vue to update DOM
                # Give Vue a moment to patch the DOM (insert img tags)
                await asyncio.sleep(0.1) 
                
                # Wait for all images to load
                try:
                    await page.wait_for_function(
                        "() => Array.from(document.images).every(img => img.complete)",
                        timeout=image_timeout
                    )
                except Exception:
                    logger.warning(f"ContentRenderer: Timeout waiting for images to load ({image_timeout}ms), taking screenshot anyway.")
                
                # Resize height if needed? 
                # The page height might change. We capture full page or specific element.
                # If capturing element:
                element = await page.query_selector("#main-container")
                if element:
                    # Clean previous screenshots? No, overwrite.
                    await element.screenshot(path=str(resolved_output_path), type="jpeg", quality=98)
                else:
                    await page.screenshot(path=str(resolved_output_path), full_page=True, type="jpeg", quality=98)
                
                self._render_count += 1
                
                duration = time.time() - start_time
                logger.success(f"ContentRenderer: Rendered in {duration:.3f}s (No.{self._render_count})")
                return True
                
            except Exception as exc:
                logger.error(f"ContentRenderer: render failed ({exc})")
                # If render failed, maybe browser is dead. Close it to force restart next time.
                await self.close()
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
        
        # 1. Extract clean URLs from markdown
        # Matches: ![...](https://...)
        img_urls = []
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', markdown):
            # Url might be followed by title: "url" "title"
            url_part = match.group(1).split()[0].strip()
            if url_part and url_part not in img_urls:
                img_urls.append(url_part)
                
        if not img_urls:
            return

        # 2. Reorder each stage's image_references
        for stage in stages:
            refs = stage.get("image_references")
            if not refs:
                continue
                
            # Map url -> ref object
            ref_map = {r["url"]: r for r in refs}
            
            new_refs = []
            seen_urls = set()
            
            # First, add images found in markdown in order
            for url in img_urls:
                if url in ref_map:
                    new_refs.append(ref_map[url])
                    seen_urls.add(url)
            
            # Then add remaining images not found in markdown
            for r in refs:
                if r["url"] not in seen_urls:
                    new_refs.append(r)
            
            stage["image_references"] = new_refs
