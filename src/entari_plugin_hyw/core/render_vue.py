"""
Vue-based Card Renderer (Minimal Python)

Python only provides raw data. All frontend logic (markdown, syntax highlighting,
math rendering, citations) is handled by the Vue frontend.
"""

import json
import gc
from pathlib import Path
from typing import List, Dict, Any

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

    async def render(
        self,
        markdown_content: str,
        output_path: str,
        stats: Dict[str, Any] = None,
        references: List[Dict[str, Any]] = None,
        page_references: List[Dict[str, Any]] = None,
        image_references: List[Dict[str, Any]] = None,
        stages_used: List[Dict[str, Any]] = None,
        **kwargs
    ) -> bool:
        """Render content to image. Python only passes raw data."""
        
        resolved_output_path = Path(output_path).resolve()
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Prepare minimal raw data - frontend handles all processing
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
        
        try:
            # Inject data into template
            # Inject data into template using regex for robustness against minification
            import re
            
            # Properly escape JSON for embedding in HTML script tag
            # Use ensure_ascii=True to convert all non-ASCII to \uXXXX escapes
            json_data = json.dumps(render_data, ensure_ascii=True)
            # Escape </script> and </Script> etc to prevent breaking out of script tag
            # Use a pattern that doesn't interfere with JSON structure
            json_data = json_data.replace("</" , r"<\/")
            
            # Check if placeholder exists
            placeholder_match = re.search(r"window\.RENDER_DATA\s*=\s*\{\}", self.template_content)
            if not placeholder_match:
                logger.error("window.RENDER_DATA = {} placeholder not found in template!")
            
            # Match window.RENDER_DATA = {} with optional whitespace
            # Use lambda to avoid re.sub interpreting backslashes in json_data
            replacement_text = f"window.RENDER_DATA = {json_data}"
            html = re.sub(
                r"window\.RENDER_DATA\s*=\s*\{\}",
                lambda m: replacement_text,
                self.template_content,
                count=1  # Only replace the first occurrence
            )
            
            logger.info(f"Data injected ({len(json_data)} bytes)")
            
            # Render with Playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        viewport={"width": 520, "height": 1400},
                        device_scale_factor=3,
                    )
                    await page.set_content(html, wait_until="networkidle")
                    await page.wait_for_timeout(200)
                    
                    element = await page.query_selector("#main-container")
                    if element:
                        await element.screenshot(path=str(resolved_output_path), type="jpeg", quality=98)
                    else:
                        await page.screenshot(path=str(resolved_output_path), full_page=True, type="jpeg", quality=98)
                    
                    return True
                finally:
                    await browser.close()
                    
        except Exception as exc:
            logger.error(f"ContentRenderer: render failed ({exc})")
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
