import asyncio
import gc
import os
import markdown
import base64
import html # Import html for escaping
import mimetypes
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Dict, Optional, Any, Union
import re
import json
from pathlib import Path
from loguru import logger
from jinja2 import Environment, FileSystemLoader, select_autoescape
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import CrawlerRunConfig
from crawl4ai.cache_context import CacheMode
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from playwright.async_api import async_playwright

# Patch Crawl4AI 0.7.x to support screenshot from raw/file HTML
async def _c4a_generate_screenshot_from_html(self, html: str) -> str:
    """
    Monkey-patched fallback: render arbitrary HTML to a screenshot using Playwright.
    """
    page, context = await self.browser_manager.get_page(
        crawlerRunConfig=CrawlerRunConfig(
            adjust_viewport_to_content=True,
            wait_until="networkidle",
            wait_for_images=True,
            cache_mode=CacheMode.BYPASS,
        )
    )
    try:
        try:
            await page.set_viewport_size({"width": 520, "height": 1200})
        except Exception:
            pass
            await page.set_content(html, wait_until="networkidle")
            await page.wait_for_timeout(150)
            element = await page.query_selector("#main-container")
        if element:
            screenshot_bytes = await element.screenshot()
        else:
            screenshot_bytes = await page.screenshot(full_page=True)
        import base64 as _b64
        return _b64.b64encode(screenshot_bytes).decode()
    finally:
        try:
            await context.close()
        except Exception:
            pass

if not hasattr(AsyncPlaywrightCrawlerStrategy, "_generate_screenshot_from_html"):
    AsyncPlaywrightCrawlerStrategy._generate_screenshot_from_html = _c4a_generate_screenshot_from_html

class ContentRenderer:
    def __init__(self, template_path: str = None):
        if template_path is None:
            # Default to assets/template.j2 in the plugin root
            current_dir = os.path.dirname(os.path.abspath(__file__))
            plugin_root = os.path.dirname(current_dir)
            template_path = os.path.join(plugin_root, "assets", "template.j2")
            
        self.template_path = template_path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        plugin_root = os.path.dirname(current_dir)
        self.assets_dir = os.path.join(plugin_root, "assets", "icon")
        
        # Load JS libraries (CSS is now inline in template)
        libs_dir = os.path.join(plugin_root, "assets", "libs")
        
        # Define all assets to load
        self.assets = {}
        assets_map = {
            "highlight_css": os.path.join(libs_dir, "highlight.css"),
            "highlight_js": os.path.join(libs_dir, "highlight.js"),
            "katex_css": os.path.join(libs_dir, "katex.css"),
            "katex_js": os.path.join(libs_dir, "katex.js"),
            "katex_auto_render_js": os.path.join(libs_dir, "katex-auto-render.js"),
            "tailwind_css": os.path.join(libs_dir, "tailwind.css"),
        }
        
        total_size = 0
        for key, path in assets_map.items():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    self.assets[key] = content
                    total_size += len(content)
            except Exception as exc:
                logger.warning(f"ContentRenderer: failed to load {key} ({exc})")
                self.assets[key] = ""
        
        logger.info(f"ContentRenderer: loaded {len(assets_map)} libs ({total_size} bytes)")

        # Initialize Jinja2 Environment
        template_dir = os.path.dirname(self.template_path)
        template_name = os.path.basename(self.template_path)
        logger.info(f"ContentRenderer: initializing Jinja2 from {template_dir} / {template_name}")
        
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml'])
        )
        self.template = self.env.get_template(template_name)

    def _get_icon_data_url(self, icon_name: str) -> str:
        if not icon_name:
            return ""
        # 1. Check if it's a URL
        if icon_name.startswith(("http://", "https://")):
            try:
                import httpx
                resp = httpx.get(icon_name, timeout=5.0)
                if resp.status_code == 200:
                    mime_type = resp.headers.get("content-type", "image/png")
                    b64_data = base64.b64encode(resp.content).decode("utf-8")
                    return f"data:{mime_type};base64,{b64_data}"
            except Exception as e:
                print(f"Failed to download icon from {icon_name}: {e}")
                # Fallback to local lookup

        # 2. Local file lookup
        filename = None
        
        if "." in icon_name:
            filename = icon_name
        else:
            # Try extensions
            for ext in [".svg", ".png"]:
                if os.path.exists(os.path.join(self.assets_dir, icon_name + ext)):
                    filename = icon_name + ext
                    break
            if not filename:
                filename = icon_name + ".svg" # Default fallback
        
        filepath = os.path.join(self.assets_dir, filename)
        
        if not os.path.exists(filepath):
            # Fallback to openai.svg if specific file not found
            filepath = os.path.join(self.assets_dir, "openai.svg")
            if not os.path.exists(filepath):
                return ""
            
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = "image/png"
            
        with open(filepath, "rb") as f:
            data = f.read()
            b64_data = base64.b64encode(data).decode("utf-8")
            return f"data:{mime_type};base64,{b64_data}"

    def _get_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            if "openrouter" in domain: return "openrouter.ai"
            if "openai" in domain: return "openai.com"
            if "anthropic" in domain: return "anthropic.com"
            if "google" in domain: return "google.com"
            if "deepseek" in domain: return "deepseek.com"
            return domain
        except:
            return "unknown"

    async def render(self, 
                     markdown_content: str, 
                     output_path: str, 
                     suggestions: List[str] = None, 
                     stats: Dict[str, Any] = None,
                     references: List[Dict[str, Any]] = None,
                     page_references: List[Dict[str, Any]] = None,
                     image_references: List[Dict[str, Any]] = None,  # Added
                     stages_used: List[Dict[str, Any]] = None,
                    flow_steps: List[Dict[str, Any]] = None,
                    model_name: str = "",
                    provider_name: str = "Unknown",
                    behavior_summary: str = "Text Generation",
                    icon_config: str = "openai",
                    vision_model_name: str = None,
                    vision_icon_config: str = None,
                    vision_base_url: str = None,
                    base_url: str = "https://openrouter.ai/api/v1",
                    billing_info: Dict[str, Any] = None,
                    render_timeout_ms: int = 6000):
        """
        Render markdown content to an image using Crawl4AI (headless) and Jinja2.
        """
        render_start_time = asyncio.get_event_loop().time()

        # Resolve output path early to avoid relative URI issues
        resolved_output_path = Path(output_path).resolve()
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Preprocess to fix common markdown issues
        markdown_content = re.sub(r'(?<=\S)\n(?=\s*(\d+\.|\-|\*|\+) )', r'\n\n', markdown_content)

        # references, page_references, image_references are already parsed by pipeline
        # No filtering needed here - use them directly

        # AGGRESSIVE CLEANING: Strip out "References" section and "[code]" blocks from the text
        # because we are rendering them as structured UI elements now.
        
        # 0. Remove content before first # heading (keep the heading)
        heading_match = re.search(r'^(#[^#])', markdown_content, re.MULTILINE)
        if heading_match:
            markdown_content = markdown_content[heading_match.start():]
        
        # 1. Remove "References" or "Citations" header and everything after it specific to the end of file
        # Matches ### References, ## References, **References**, etc., followed by list items
        markdown_content = re.sub(r'(?i)^\s*(#{1,3}|\*\*)\s*(References|Citations|Sources).*$', '', markdown_content, flags=re.MULTILINE | re.DOTALL)
        
        # 2. Remove isolated "[code] ..." lines (checking for the specific format seen in user screenshot)
        # Matches lines starting with [code] or [CODE]
        markdown_content = re.sub(r'(?i)^\s*\[code\].*?(\n|$)', '', markdown_content, flags=re.MULTILINE)

        max_attempts = 1
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                # 1. Protect math blocks
                # We look for $$...$$, \[...\], \(...\)
                # We'll replace them with placeholders so markdown extensions (like nl2br) don't touch them.
                math_blocks = {}
                
                def protect_math(match):
                    key = f"MATHBLOCK{len(math_blocks)}PLACEHOLDER"
                    # Escape ONLY < and > to prevent them from being parsed as HTML tags
                    # We preserve & and other chars to avoid breaking LaTeX alignment
                    escaped_math = match.group(0).replace("<", "&lt;").replace(">", "&gt;")
                    math_blocks[key] = escaped_math
                    return key

                # Patterns for math:
                # 1) $$ ... $$ (display math)
                # 2) \[ ... \] (display math)
                # 3) \( ... \) (inline math)
                # Note: We must handle multiline for $$ and \[
                
                # Regex for $$...$$
                markdown_content = re.sub(r'\$\$(.*?)\$\$', protect_math, markdown_content, flags=re.DOTALL)
                
                # Regex for \[...\]
                markdown_content = re.sub(r'\\\[(.*?)\\\]', protect_math, markdown_content, flags=re.DOTALL)
                
                # Regex for \(...\) (usually single line, but DOTALL is safest if user wraps lines)
                markdown_content = re.sub(r'\\\((.*?)\\\)', protect_math, markdown_content, flags=re.DOTALL)

                # 2. Render Markdown
                # Use 'nl2br' to turn newlines into <br>, 'fenced_code' for code blocks
                content_html = markdown.markdown(
                    markdown_content.strip(),
                    extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
                )
                
                # 3. Restore math blocks
                def restore_math(text):
                    # We assume placeholders are intact. We do a simple string replace or regex.
                    # Since placeholders are unique strings, we can just replace them.
                    for key, val in math_blocks.items():
                        text = text.replace(key, val)
                    return text

                content_html = restore_math(content_html)
                
                # Convert [search:N] to blue badge
                content_html = re.sub(
                    r'\[search:(\d+)\]',
                    r'<span class="inline-flex items-center justify-center min-w-[16px] h-4 px-0.5 text-[10px] font-bold text-blue-600 bg-blue-50 border border-blue-200 rounded mx-0.5 align-top relative -top-0.5">\1</span>',
                    content_html
                )
                # Convert [page:N] to orange badge
                content_html = re.sub(
                    r'\[page:(\d+)\]',
                    r'<span class="inline-flex items-center justify-center min-w-[16px] h-4 px-0.5 text-[10px] font-bold text-orange-700 bg-orange-50 border border-orange-200 rounded mx-0.5 align-top relative -top-0.5">\1</span>',
                    content_html
                )
                
                # Strip out the references code block if it leaked into the content
                content_html = re.sub(r'<pre><code[^>]*>.*?references.*?</code></pre>\s*$', '', content_html, flags=re.DOTALL | re.IGNORECASE)

                # --- PREPARE DATA FOR JINJA TEMPLATE ---
                
                # 1. Pipeline Stages (with Nested Data)
                processed_stages = []
                
                # Unified Search Icon (RemixIcon)
                SEARCH_ICON = '<i class="ri-search-line text-[16px]"></i>'
                BROWSER_ICON = '<i class="ri-global-line text-[16px]"></i>'
                DEFAULT_ICON = '<i class="ri-box-3-line text-[16px]"></i>'

                # Helper to infer provider/icon name from model string
                def infer_icon_name(model_str):
                    if not model_str: return None
                    m = model_str.lower()
                    if "claude" in m or "anthropic" in m: return "anthropic"
                    if "gpt" in m or "openai" in m or "o1" in m: return "openai"
                    if "gemini" in m or "google" in m: return "google"
                    if "deepseek" in m: return "deepseek"
                    if "mistral" in m: return "mistral"
                    if "llama" in m: return "meta"
                    if "qwen" in m: return "qwen"
                    if "grok" in m: return "grok"
                    if "perplexity" in m: return "perplexity"
                    if "minimax" in m: return "minimax"
                    if "nvidia" in m: return "nvidia"
                    return None

                # 2. Reference Processing (Moved up for nesting)
                processed_refs = []
                if references:
                    for ref in references[:8]:
                        url = ref.get("url", "#")
                        try:
                            domain = urlparse(url).netloc
                            if domain.startswith("www."): domain = domain[4:]
                        except:
                            domain = "unknown"
                        
                        processed_refs.append({
                            "title": ref.get("title", "No Title"),
                            "url": url,
                            "domain": domain,
                            "favicon_url": f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
                        })

                # 2b. Page Reference Processing (crawled pages)
                processed_page_refs = []
                if page_references:
                    for ref in page_references[:8]:
                        url = ref.get("url", "#")
                        try:
                            domain = urlparse(url).netloc
                            if domain.startswith("www."): domain = domain[4:]
                        except:
                            domain = "unknown"
                        
                        processed_page_refs.append({
                            "title": ref.get("title", "No Title"),
                            "url": url,
                            "domain": domain,
                            "favicon_url": f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
                        })

                # 2c. Image Reference Processing
                processed_image_refs = []
                if image_references:
                     for ref in image_references[:8]:
                         url = ref.get("url", "#")
                         processed_image_refs.append({
                             "title": ref.get("title", "Image"),
                             "url": url,
                             "thumbnail": ref.get("thumbnail") or url, # Fallback to url if thumbnail not provided
                             "domain": self._get_domain(url) or ref.get("domain") or "image"
                         })

                flow_steps = flow_steps or []

                if stages_used:
                    for stage in stages_used:
                        name = stage.get("name", "Step")
                        model = stage.get("model", "")
                        
                        icon_html = ""
                        
                        if name == "Search":
                             icon_html = SEARCH_ICON
                        elif name == "Crawler":
                             icon_html = BROWSER_ICON
                        else:
                            # Try to find vendor logo
                            # 1. Check explicit icon_config
                            icon_key = stage.get("icon_config", "")
                            # 2. Infer from model name if not present
                            if not icon_key:
                                icon_key = infer_icon_name(model)
                            
                            icon_data_url = ""
                            if icon_key:
                                icon_data_url = self._get_icon_data_url(icon_key)
                                
                            if icon_data_url:
                                icon_html = f'<img src="{icon_data_url}" class="w-5 h-5 object-contain rounded">'
                            else:
                                icon_html = DEFAULT_ICON
                        
                        # Model Short
                        model_short = model.split("/")[-1] if "/" in model else model
                        if len(model_short) > 25:
                            model_short = model_short[:23] + "…"

                        time_val = stage.get("time", 0)
                        cost_val = stage.get("cost", 0.0)
                        if name == "Search": cost_val = 0.0
                        
                        # --- NESTED DATA ---
                        stage_children = {}
                        
                        # References go to "Search"
                        # Also Image References to "Search"
                        if name == "Search":
                            if processed_refs:
                                stage_children['references'] = processed_refs
                            if processed_image_refs:
                                stage_children['image_references'] = processed_image_refs

                        # Flow steps go to "Agent"
                        if name == "Agent" and flow_steps:
                            FLOW_ICONS = {
                                "search": SEARCH_ICON,
                                "page": '<i class="ri-file-text-line text-[16px]"></i>',
                            }
                            formatted_flow = []
                            for step in flow_steps:
                                icon_key = step.get("icon", "").lower()
                                formatted_flow.append({
                                    "icon_svg": FLOW_ICONS.get(icon_key, FLOW_ICONS.get("search")),
                                    "description": step.get("description", "")
                                })
                            stage_children['flow_steps'] = formatted_flow

                        # Pass through Search Queries
                        if "queries" in stage:
                            stage_children["queries"] = stage["queries"]
                            
                        # Pass through Crawled Pages
                        if "crawled_pages" in stage:
                            stage_children["crawled_pages"] = stage["crawled_pages"]

                        processed_stages.append({
                            "name": name,
                            "model": model,
                            "model_short": model_short,
                            "provider": stage.get("provider", ""),
                            "icon_html": icon_html,
                            "time_str": f"{time_val:.2f}s",
                            "cost_str": f"${cost_val:.6f}" if cost_val > 0 else "$0",
                            **stage_children # Merge children
                        })

                # Ensure references are displayed even if no "Search" stage was present
                has_search_stage = any(s.get("name") == "Search" for s in processed_stages)
                if not has_search_stage and (processed_refs or processed_image_refs):
                    # Create a virtual Search stage
                    virtual_search = {
                        "name": "Search",
                        "model": "DuckDuckGo", # Default assumption
                        "model_short": "DuckDuckGo",
                        "provider": "Reference",
                        "icon_html": SEARCH_ICON,
                        "time_str": "0.00s",
                        "cost_str": "$0",
                    }
                    if processed_refs:
                        virtual_search['references'] = processed_refs
                    if processed_image_refs:
                        virtual_search['image_references'] = processed_image_refs
                    
                    # Insert after Vision/Instruct (usually index 0 or 1), or at start
                    insert_idx = 0
                    if processed_stages and processed_stages[0]["name"] in ["Vision", "Instruct"]:
                        insert_idx = 1
                        if len(processed_stages) > 1 and processed_stages[1]["name"] == "Instruct":
                            insert_idx = 2
                    
                    processed_stages.insert(insert_idx, virtual_search)

                # 4. Stats Footer Logic
                processed_stats = {}
                stats_dict = {}
                if stats:
                     # Assuming standard 'stats' dict structure, handle list if needed
                    if isinstance(stats, list):
                        stats_dict = stats[0] if stats else {}
                    else:
                        stats_dict = stats
                    
                    agent_total_time = stats_dict.get("time", 0)
                    vision_time = stats_dict.get("vision_duration", 0)
                    llm_time = max(0, agent_total_time - vision_time)
                    
                    vision_html = ""
                    if vision_time > 0:
                        vision_html = f'''
                        <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                            <span class="w-2 h-2 rounded-full bg-purple-400"></span>
                            <span>{vision_time:.1f}s</span>
                        </div>
                        '''
                    
                    llm_html = f'''
                    <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                        <span class="w-2 h-2 rounded-full bg-green-400"></span>
                        <span>{llm_time:.1f}s</span>
                    </div>
                    '''
                    
                    billing_html = ""
                    if billing_info and billing_info.get("total_cost", 0) > 0:
                        cost_cents = billing_info["total_cost"] * 100
                        billing_html = f'''
                        <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                            <span class="w-2 h-2 rounded-full bg-pink-500"></span>
                            <span>{cost_cents:.4f}¢</span>
                        </div>
                        '''

                    processed_stats = {
                        "vision_html": vision_html,
                        "llm_html": llm_html,
                        "billing_html": billing_html
                    }

                # 5. Feature Flags for Header Icons
                feature_flags = {
                    "has_vision": False,
                    "has_search": False,
                }
                
                # Check Vision
                if stats_dict.get("vision_duration", 0) > 0:
                    feature_flags["has_vision"] = True
                
                # Check Search
                if any(s.get("name") == "Search" for s in stages_used or []):
                    feature_flags["has_search"] = True
                
                # Render Template
                context = {
                    "content_html": content_html,
                    "suggestions": suggestions or [],
                    "stages": processed_stages,
                    "references": processed_refs,
                    "page_references": processed_page_refs,
                    "references_json": json.dumps(references or []),
                    "stats": processed_stats,
                    "flags": feature_flags,
                    "total_time": stats_dict.get("total_time", 0) or 0,
                    **self.assets
                }
                
                final_html = self.template.render(**context)

            except MemoryError:
                last_exc = "memory"
                logger.warning(f"ContentRenderer: out of memory while building HTML (attempt {attempt}/{max_attempts})")
                continue
            except Exception as exc:
                last_exc = exc
                logger.warning(f"ContentRenderer: failed to build HTML (attempt {attempt}/{max_attempts}) ({exc})")
                continue
            
            try:
                # Use Playwright directly for crisp element screenshot (Crawl4AI already depends on it)
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    try:
                        page = await browser.new_page(
                            viewport={"width": 520, "height": 1400},
                            device_scale_factor=3,
                        )
                        await page.set_content(final_html, wait_until="networkidle")
                        await page.wait_for_timeout(150)
                        element = await page.query_selector("#main-container")
                        if element:
                            await element.screenshot(path=resolved_output_path, type="jpeg", quality=98)
                        else:
                            await page.screenshot(path=resolved_output_path, full_page=True, type="jpeg", quality=98)
                        return True
                    finally:
                        await browser.close()

            except Exception as exc:
                last_exc = exc
                logger.warning(f"ContentRenderer: render attempt {attempt}/{max_attempts} failed ({exc})")
            finally:
                content_html = None
                final_html = None
                gc.collect()

        logger.error(f"ContentRenderer: render failed after {max_attempts} attempts ({last_exc})")
        return False

    async def render_models_list(
        self,
        models: List[Dict[str, Any]],
        output_path: str,
        default_base_url: str = "https://openrouter.ai/api/v1",
        render_timeout_ms: int = 6000,
    ) -> bool:
        """
        Lightweight models list renderer leveraging the main render pipeline.
        """
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
            suggestions=[],
            stats={"time": 0.0},
            references=[],
            stages_used=[],
            model_name="",
            provider_name="Models",
            behavior_summary="Model List",
            icon_config="openai",
            base_url=default_base_url,
            billing_info=None,
            render_timeout_ms=render_timeout_ms,
        )
