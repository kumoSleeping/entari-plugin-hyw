import asyncio
import gc
import os
import markdown
import base64
import mimetypes
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Dict, Optional, Any, Union
import re
import json
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from loguru import logger
from jinja2 import Environment, FileSystemLoader, select_autoescape

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

    async def _set_content_safe(self, page, html: str, timeout_ms: int) -> bool:
        html_size = len(html)
        try:
            await page.set_content(html, wait_until="networkidle", timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            logger.warning(f"ContentRenderer: page.set_content timed out after {timeout_ms}ms (html_size={html_size})")
            return False
        except Exception as exc:
            logger.warning(f"ContentRenderer: page.set_content failed (html_size={html_size}): {exc}")
            return False
    
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
                    mcp_steps: List[Dict[str, Any]] = None,
                    stages_used: List[Dict[str, Any]] = None,
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
        Render markdown content to an image using Playwright and Jinja2.
        """
        render_start_time = asyncio.get_event_loop().time()
        
        # Preprocess to fix common markdown issues
        markdown_content = re.sub(r'(?<=\S)\n(?=\s*(\d+\.|[-*+]) )', r'\n\n', markdown_content)

        # AGGRESSIVE CLEANING: Strip out "References" section and "[code]" blocks from the text
        # because we are rendering them as structured UI elements now.
        
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
                    key = f"__MATH_BLOCK_{len(math_blocks)}__"
                    math_blocks[key] = match.group(0)
                    return key

                # Patterns for math:
                # 1) $$ ... $$ (display math)
                # 2) \[ ... \] (display math)
                # 3) \( ... \) (inline math)
                # Note: We must handle multiline for $$ and \[
                
                # Regex for $$...$$
                markdown_content = re.sub(r'\$\$(.*?)\$\$\s*', protect_math, markdown_content, flags=re.DOTALL)
                
                # Regex for \[...\]
                markdown_content = re.sub(r'\\\[(.*?)\\\]\s*', protect_math, markdown_content, flags=re.DOTALL)
                
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
                
                # Post-process to style citation markers
                parts = re.split(r'(<code.*?>.*?</code>)', content_html, flags=re.DOTALL)
                for i, part in enumerate(parts):
                    if not part.startswith('<code'):
                        # 1. Numeric Citations [references](/1) -> Blue Style (References)
                        part = re.sub(r'\[references\]\(/(\d+)\)', r'<span class="inline-flex items-center justify-center min-w-[16px] h-4 px-0.5 text-[10px] font-bold text-blue-600 bg-blue-50 border border-blue-200 rounded mx-0.5 align-top relative -top-0.5">\1</span>', part)
                        # 2. Alphabetical Citations [mcp](/a) -> Orange Style (MCP Flow)
                        part = re.sub(r'\[mcp\]\(/([a-zA-Z]+)\)', r'<span class="inline-flex items-center justify-center min-w-[16px] h-4 px-0.5 text-[10px] font-bold text-orange-600 bg-orange-50 border border-orange-200 rounded mx-0.5 align-top relative -top-0.5">\1</span>', part)
                        parts[i] = part
                content_html = "".join(parts)
                
                # Strip out the structured JSON blocks if they leaked into the content
                # Look for <pre>... containing "mcp_steps" or "references" at the end
                # Make regex robust to any language class or no class
                content_html = re.sub(r'<pre><code[^>]*>[^<]*(mcp_steps|references)[^<]*</code></pre>\s*$', '', content_html, flags=re.DOTALL | re.IGNORECASE)
                # Loop to remove multiple if present
                while re.search(r'<pre><code[^>]*>[^<]*(mcp_steps|references)[^<]*</code></pre>\s*$', content_html, flags=re.DOTALL | re.IGNORECASE):
                    content_html = re.sub(r'<pre><code[^>]*>[^<]*(mcp_steps|references)[^<]*</code></pre>\s*$', '', content_html, flags=re.DOTALL | re.IGNORECASE)

                # --- PREPARE DATA FOR JINJA TEMPLATE ---
                
                # 1. Pipeline Stages (with Nested Data)
                processed_stages = []
                
                # Unified Search Icon (RemixIcon)
                SEARCH_ICON = '<i class="ri-search-line text-[16px]"></i>'
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

                if stages_used:
                    for stage in stages_used:
                        name = stage.get("name", "Step")
                        model = stage.get("model", "")
                        
                        icon_html = ""
                        
                        if name == "Search":
                             icon_html = SEARCH_ICON
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
                        if name == "Search" and processed_refs:
                            stage_children['references'] = processed_refs
                            
                        # MCP Steps go to "Agent"
                        # Process MCP steps here for the template
                        stage_mcp_steps = []
                        if name == "Agent" and mcp_steps:
                             # RemixIcon Mapping
                            STEP_ICONS = {
                                "navigate": '<i class="ri-compass-3-line"></i>',
                                "snapshot": '<i class="ri-camera-lens-line"></i>',
                                "click": '<i class="ri-cursor-fill"></i>',
                                "type": '<i class="ri-keyboard-line"></i>',
                                "code": '<i class="ri-code-line"></i>',
                                "search": SEARCH_ICON, 
                                "default": '<i class="ri-arrow-right-s-line"></i>',
                            }
                            for step in mcp_steps:
                                icon_key = step.get("icon", "").lower()
                                if "search" in icon_key: icon_key = "search"
                                elif "nav" in icon_key or "visit" in icon_key: icon_key = "navigate"
                                elif "click" in icon_key: icon_key = "click"
                                elif "type" in icon_key or "input" in icon_key: icon_key = "type"
                                elif "shot" in icon_key: icon_key = "snapshot"
                                
                                stage_mcp_steps.append({
                                    "name": step.get("name", "unknown"),
                                    "description": step.get("description", ""),
                                    "icon_svg": STEP_ICONS.get(icon_key, STEP_ICONS["default"])
                                })
                            stage_children['mcp_steps'] = stage_mcp_steps

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





                # 4. Stats Footer Logic
                processed_stats = {}
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

                # Render Template
                context = {
                    "content_html": content_html,
                    "suggestions": suggestions or [],
                    "stages": processed_stages,
                    "references": processed_refs,
                    "references_json": json.dumps(references or []),
                    "stats": processed_stats,
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
                # logger.info("ContentRenderer: launching playwright...")
                async with async_playwright() as p:
                    # logger.info("ContentRenderer: playwright context ready, launching browser...")
                    browser = await p.chromium.launch(headless=True)
                    try:
                        # Use device_scale_factor=2 for high DPI rendering (better quality)
                        page = await browser.new_page(viewport={"width": 450, "height": 1200}, device_scale_factor=2)
                        
                        # Set content (10s timeout to handle slow CDN loading)
                        set_ok = await self._set_content_safe(page, final_html, 10000)
                        if not set_ok or page.is_closed():
                            raise RuntimeError("set_content failed")
                        
                        # Wait for images with user-configured timeout (render_timeout_ms)
                        image_timeout_sec = render_timeout_ms / 1000.0
                        try:
                            await asyncio.wait_for(
                                page.evaluate("""
                                    () => Promise.all(
                                        Array.from(document.images).map(img => {
                                            if (img.complete) {
                                                if (img.naturalWidth === 0 || img.naturalHeight === 0) {
                                                    img.style.display = 'none';
                                                }
                                                return Promise.resolve();
                                            }
                                            return new Promise((resolve) => {
                                                img.onload = () => {
                                                    if (img.naturalWidth === 0 || img.naturalHeight === 0) {
                                                        img.style.display = 'none';
                                                    }
                                                    resolve();
                                                };
                                                img.onerror = () => {
                                                    img.style.display = 'none';
                                                    resolve();
                                                };
                                            });
                                        })
                                    )
                                """),
                                timeout=image_timeout_sec
                            )
                        except asyncio.TimeoutError:
                            logger.warning(f"ContentRenderer: image loading timed out after {image_timeout_sec}s, continuing...")
                        
                        # Brief wait for layout to stabilize
                        await asyncio.sleep(0.1)
                        
                        # Try element screenshot first, fallback to full page
                        element = await page.query_selector("#main-container")
                        
                        try:
                            if element:
                                await element.screenshot(path=output_path)
                            else:
                                await page.screenshot(path=output_path, full_page=True)
                        except Exception as screenshot_exc:
                            logger.warning(f"ContentRenderer: element screenshot failed ({screenshot_exc}), trying full page...")
                            await page.screenshot(path=output_path, full_page=True)
                        
                    finally:
                        try:
                            await browser.close()
                        except Exception as exc:
                            logger.warning(f"ContentRenderer: failed to close browser ({exc})")
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning(f"ContentRenderer: render attempt {attempt}/{max_attempts} failed ({exc})")
            finally:
                content_html = None
                final_html = None
                gc.collect()
