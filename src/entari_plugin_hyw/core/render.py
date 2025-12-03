import asyncio
import os
import markdown
import base64
import mimetypes
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Dict, Optional, Any, Union
import re
import json
from playwright.async_api import async_playwright

class ContentRenderer:
    def __init__(self, template_path: str = None):
        if template_path is None:
            # Default to assets/template.html in the plugin root
            current_dir = os.path.dirname(os.path.abspath(__file__))
            plugin_root = os.path.dirname(current_dir)
            template_path = os.path.join(plugin_root, "assets", "template.html")
        self.template_path = template_path
        # Re-resolve plugin_root if template_path was passed, or just use the logic above
        current_dir = os.path.dirname(os.path.abspath(__file__))
        plugin_root = os.path.dirname(current_dir)
        self.assets_dir = os.path.join(plugin_root, "assets", "icon")


    
    def _get_icon_data_url(self, icon_name: str) -> str:
        if not icon_name:
            return ""
        # 1. Check if it's a URL
        if icon_name.startswith(("http://", "https://")):
            try:
                import httpx
                # Synchronous download for simplicity in this context, or use async if possible.
                # Since this method is not async, we use httpx.get (sync).
                # Note: This might block the event loop slightly, but usually icons are small.
                # Ideally this method should be async, but that requires refactoring callers.
                # Given the constraints, we'll try a quick sync fetch with timeout.
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

    def _generate_card_header(self, title: str, icon_data_url: str = None, icon_emoji: str = None, custom_icon_html: str = None, badge_text: str = None, badge_class: str = "llm", provider_text: str = None, vision_title: str = None, vision_icon_data_url: str = None, vision_provider_text: str = None, is_plain: bool = False) -> str:
        # LLM Icon
        icon_html = ""
        if custom_icon_html:
            icon_html = custom_icon_html
        elif icon_data_url:
            icon_html = f'<img src="{icon_data_url}" class="w-5 h-5 object-contain">'
        elif icon_emoji:
             icon_html = f'<span class="text-lg shrink-0">{icon_emoji}</span>'
        elif badge_text: # Fallback icon based on badge type if no image
             icon_symbol = "🔎" if "search" in badge_class else "🤖"
             icon_html = f'<span class="text-lg shrink-0">{icon_symbol}</span>'

        # Badge (e.g. Search)
        badge_html = ""
        if badge_text:
             # Map badge class to tailwind colors
             bg_color = "bg-blue-50"
             text_color = "text-blue-700"
             if "search" in badge_class:
                 bg_color = "bg-pink-50"
                 text_color = "text-pink-700"
             elif "vision" in badge_class:
                 bg_color = "bg-rose-50"
                 text_color = "text-rose-600"
                 
             badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold uppercase tracking-wide {bg_color} {text_color}">{badge_text}</span>'
        
        # Provider Badge
        provider_html = ""
        if provider_text:
            provider_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium uppercase tracking-wide bg-gray-100 text-gray-500 lowercase">{provider_text}</span>'

        # Determine main badge styles
        main_badge_bg = "bg-white"
        title_color = "text-pink-600"
        if vision_title:
            main_badge_bg = "bg-white"
            
        if is_plain:
            title_color = "text-gray-900"

        # Main Title Group
        main_title_group = ""
        if title:
            main_title_group = f'''
                <div class="{main_badge_bg} shadow-sm rounded-md px-2.5 py-1 flex-none flex items-center gap-2 { '!bg-transparent !shadow-none !p-0' if is_plain else '' }">
                    {icon_html}
                    <span class="text-sm font-bold {title_color} uppercase tracking-wide whitespace-nowrap overflow-hidden text-ellipsis">{title}</span>
                </div>
            '''

        # Vision Title Group (if present)
        vision_row_html = ""
        if vision_title:
            vision_icon_html = ""
            if vision_icon_data_url:
                vision_icon_html = f'<img src="{vision_icon_data_url}" class="w-5 h-5 object-contain">'
            else:
                vision_icon_html = '<span class="text-lg shrink-0">👁️</span>'
            
            vision_provider_html = ""
            if vision_provider_text:
                vision_provider_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium uppercase tracking-wide bg-gray-100 text-gray-500 lowercase">{vision_provider_text}</span>'
                
            vision_row_html = f'''
            <div class="mb-2 flex items-center justify-between w-full">
                <div class="bg-gradient-to-r from-pink-300 to-pink-200 text-white w-fit rounded-md px-2.5 py-1 flex items-center gap-2 flex-none">
                    {vision_icon_html}
                    <span class="text-sm font-bold text-white uppercase tracking-wide whitespace-nowrap overflow-hidden text-ellipsis">{vision_title}</span>
                </div>
                <div class="flex items-center gap-2">
                    {vision_provider_html}
                </div>
            </div>
            '''

        # If no main title (vision only), we might need to adjust layout
        main_row_html = ""
        if main_title_group:
             main_row_html = f'''
                <div class="flex items-center justify-between w-full">
                    {main_title_group}
                    <div class="flex items-center gap-2">
                        {provider_html}
                        {badge_html}
                    </div>
                </div>
             '''

        return f'''
        <div class="flex items-center justify-between pb-3 mb-3 border-b border-gray-100 gap-3 { '!bg-transparent !border-none !p-0 !mb-0 !pb-0' if is_plain else '' }">
            <div class="flex flex-col w-full">
                {vision_row_html}
                {main_row_html}
            </div>
        </div>
        '''

    def _generate_suggestions_html(self, suggestions: List[str]) -> str:
        if not suggestions:
            return ""
            
        # Pink Sparkles SVG
        icon_svg = '''
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5 text-pink-500">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z" />
        </svg>
        '''
        header = self._generate_card_header("SUGGESTIONS", badge_text=None, custom_icon_html=icon_svg, is_plain=True)
            
        html_parts = ['<div class="flex flex-col gap-2 bg-[#f2f2f2] rounded-2xl p-5 overflow-hidden">']
        html_parts.append(header)
        html_parts.append('<div class="grid grid-cols-2 gap-2.5">')
        
        for i, sug in enumerate(suggestions):
            html_parts.append(f'''
            <div class="flex items-baseline gap-2 text-sm text-gray-600 px-3.5 py-2.5 bg-white/80 backdrop-blur-sm rounded-full shadow-sm hover:shadow transition-shadow cursor-default">
                <span class="text-pink-600 font-mono font-bold text-[13px] whitespace-nowrap">{i+1}</span>
                <span class="flex-1 text-[13px] text-gray-600 font-medium whitespace-nowrap overflow-hidden text-ellipsis">{sug}</span>
            </div>
            ''')
        html_parts.append('</div></div>')
        return "".join(html_parts)

    def _generate_status_footer(self, stats: Union[Dict[str, Any], List[Dict[str, Any]]], session_id: str, turns: int, max_turns: int = 10) -> str:
        if not stats:
            return ""
            
        # Check if multi-step
        is_multi_step = isinstance(stats, list)
        
        if is_multi_step:
            # Multi-step Layout
            # stats is [step1_stats, step2_stats]
            step1_stats = stats[0]
            step2_stats = stats[1] if len(stats) > 1 else {}
            
            step1_time = step1_stats.get("time", 0)
            step2_time = step2_stats.get("time", 0)
            
            total_tool_count = step1_stats.get("tool_calls_count", 0) + step2_stats.get("tool_calls_count", 0)
            
            # Step 1 Time (Purple)
            step1_html = f'''
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-purple-400"></span>
                <span>{step1_time:.1f}s</span>
            </div>
            '''
            
            # Step 2 Time (Green)
            step2_html = f'''
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-green-400"></span>
                <span>{step2_time:.1f}s</span>
            </div>
            '''
            
            # Render Time (Orange)
            render_html = f'''
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-orange-400"></span>
                <span id="render-time-display">...</span>
            </div>
            '''
            
            # Total Time (Gray)
            total_html = f'''
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-gray-400"></span>
                <span id="total-time-display">...</span>
            </div>
            '''
            
            # Tools, Turns, ID
            extras_html = f'''
            <div class="w-[1px] h-4 bg-gray-300 mx-1"></div>

            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-blue-400"></span>
                <span>{total_tool_count}</span>
            </div>
            
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-pink-400"></span>
                <span>{turns}</span>
            </div>
            
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm ml-auto">
                <span class="text-pink-600">{session_id}</span>
            </div>
            '''
            
            return f'''
            <div class="flex flex-col gap-2 bg-[#f2f2f2] rounded-2xl p-3 overflow-hidden">
                <div class="flex flex-wrap items-center gap-2 text-[10px] text-gray-600 font-bold font-mono uppercase tracking-wide">
                    {step1_html}
                    {step2_html}
                    {render_html}
                    {total_html}
                    {extras_html}
                </div>
            </div>
            '''
            
        else:
            # Single Step Layout (Existing Logic)
            agent_total_time = stats.get("time", 0)
            vision_time = stats.get("vision_duration", 0)
            llm_time = max(0, agent_total_time - vision_time)
            tool_count = stats.get("tool_calls_count", 0)
            
            # Vision Time Block
            vision_html = ""
            if vision_time > 0:
                vision_html = f'''
                <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                    <span class="w-2 h-2 rounded-full bg-purple-400"></span>
                    <span>{vision_time:.1f}s</span>
                </div>
                '''
                
            # Agent Time Block
            agent_html = f'''
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-green-400"></span>
                <span>{llm_time:.1f}s</span>
            </div>
            '''
            
            return f'''
            <div class="flex flex-col gap-2 bg-[#f2f2f2] rounded-2xl p-3 overflow-hidden">
                <div class="flex flex-wrap items-center gap-2 text-[10px] text-gray-600 font-bold font-mono uppercase tracking-wide">
                    {vision_html}
                    {agent_html}

                    <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                        <span class="w-2 h-2 rounded-full bg-orange-400"></span>
                        <span id="render-time-display">...</span>
                    </div>

                    <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                        <span class="w-2 h-2 rounded-full bg-gray-400"></span>
                        <span id="total-time-display">...</span>
                    </div>
                    
                    <div class="w-[1px] h-4 bg-gray-300 mx-1"></div>

                    <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                        <span class="w-2 h-2 rounded-full bg-blue-400"></span>
                        <span>{tool_count}</span>
                    </div>
                    
                    <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                        <span class="w-2 h-2 rounded-full bg-pink-400"></span>
                        <span>{turns}</span>
                    </div>
                    
                    <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm ml-auto">
                        <span class="text-pink-600">{session_id}</span>
                    </div>
                </div>
            </div>
            '''

    def _generate_references_html(self, references: List[Dict[str, Any]], search_provider: str) -> str:
        if not references:
            return ""
            
        # Limit to 8 references
        refs = references[:8]
        
        # provider_display = search_provider.replace("_", " ").title()
        provider_display = "REFERENCES"
        
        # Pink Globe SVG
        icon_svg = '''
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5 text-pink-500">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S12 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S12 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418" />
        </svg>
        '''
        header = self._generate_card_header(provider_display, badge_text=None, custom_icon_html=icon_svg, is_plain=True) # Title is the provider
            
        html_parts = ['<div class="flex flex-col gap-3 bg-[#f2f2f2] rounded-2xl p-5 overflow-hidden">']
        html_parts.append(header)
        html_parts.append('<div class="grid grid-cols-2 gap-2.5">')
        
        for i, ref in enumerate(refs):
            title = ref.get("title", "No Title")
            url = ref.get("url", "#")
            try:
                domain = urlparse(url).netloc
            except Exception:
                domain = "unknown"
                
            favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
            
            html_parts.append(f'''
            <a href="{url}" class="flex items-center gap-2.5 p-2.5 bg-white rounded-lg border border-gray-100 no-underline text-inherit transition-colors hover:bg-gray-50 shadow-sm" target="_blank">
                <div class="w-5 h-5 bg-pink-50 text-pink-600 border border-pink-100 rounded-md flex items-center justify-center text-[11px] font-bold shrink-0">{i+1}</div>
                <div class="flex-1 overflow-hidden flex flex-col gap-0.5">
                    <div class="text-[13px] font-semibold text-gray-800 line-clamp-2" title="{title}">{title}</div>
                    <div class="text-[11px] text-gray-400 flex items-center gap-1 whitespace-nowrap overflow-hidden text-ellipsis">
                        <img src="{favicon_url}" class="w-3 h-3 rounded-sm" onerror="this.style.display='none'">
                        {domain}
                    </div>
                </div>
            </a>
            ''')
            
        html_parts.append('</div></div>')
        return "".join(html_parts)

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
                     model_name: str = "",
                     search_provider: str = "Unknown Provider",
                     icon_config: str = "openai",
                     vision_model_name: str = None,
                     vision_icon_config: str = None,
                     vision_base_url: str = None,
                     base_url: str = "https://openrouter.ai/api/v1",
                     session_id: str = "N/A",
                     turns: int = 1,
                     max_turns: int = 10):
        """
        Render markdown content to an image using Playwright.
        """
        render_start_time = asyncio.get_event_loop().time()
        
        # Preprocess to fix common markdown issues (like lists without preceding newline)
        # Ensure a blank line before a list item if the previous line is not empty
        # Matches a newline preceded by non-whitespace, followed by a list marker
        markdown_content = re.sub(r'(?<=\S)\n(?=\s*(\d+\.|[-*+]) )', r'\n\n', markdown_content)
        
        # Replace Chinese colon with English colon + space to avoid list rendering issues
        # markdown_content = markdown_content.replace("：", ":")
        
        # Replace other full-width punctuation with half-width + space
        # markdown_content = markdown_content.replace("，", ",").replace("。", ".").replace("？", "?").replace("！", "!")
        
        # Remove bold markers around Chinese text (or text containing CJK characters)
        # This addresses rendering issues where bold Chinese fonts look bad or fail to render.
        # Matches **...** where content includes at least one CJK character.
        # markdown_content = re.sub(r'\*\*([^*]*[\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef][^*]*)\*\*', r'\1', markdown_content)

        # 1. Server-side Markdown Rendering
        # Convert markdown to HTML using Python's markdown library
        # We use extensions to support common features
        content_html = markdown.markdown(
            markdown_content.strip(), 
            extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
        )
        
        # Post-process to style citation markers [1], [2]...
        # We avoid replacing inside <code> tags by splitting the HTML
        parts = re.split(r'(<code.*?>.*?</code>)', content_html, flags=re.DOTALL)
        for i, part in enumerate(parts):
            if not part.startswith('<code'):
                # Replace [n] with styled superscript, avoiding HTML attributes
                parts[i] = re.sub(r'\[(\d+)\](?![^<]*>)', r'<sup class="text-pink-600 font-bold text-[10px] ml-0.5">\1</sup>', part)
        content_html = "".join(parts)
        
        # 2. Prepare Template Variables
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Header for Response Card
        if "/" in model_name:
            model_display = model_name.split("/")[-1].upper()
        else:
            model_display = model_name.upper()
            
        icon_data_url = self._get_icon_data_url(icon_config)
        
        provider_domain = self._get_domain(base_url)
        
        # Prepare Vision Info if vision model was used
        vision_display = None
        vision_icon_url = None
        vision_provider_domain = None
        
        if vision_model_name:
            if "/" in vision_model_name:
                vision_display = vision_model_name.split("/")[-1].upper()
            else:
                vision_display = vision_model_name.upper()
            
            # Use provided icon config or default to 'openai' (or generic eye icon logic inside _get_icon_data_url if we pass a generic name)
            # Actually _get_icon_data_url handles fallback to openai.svg if file not found.
            # But we need to pass something.
            v_icon = vision_icon_config if vision_icon_config else "openai"
            vision_icon_url = self._get_icon_data_url(v_icon)
            
            vision_provider_domain = self._get_domain(vision_base_url or base_url)
        
        response_header = self._generate_card_header(
            model_display, 
            icon_data_url=icon_data_url, 
            provider_text=provider_domain,
            vision_title=vision_display,
            vision_icon_data_url=vision_icon_url,
            vision_provider_text=vision_provider_domain
        )
        
        suggestions_html = self._generate_suggestions_html(suggestions or [])
        stats_html = self._generate_status_footer(stats or {}, session_id, turns, max_turns)
        # Pass search_provider to references generation
        references_html = self._generate_references_html(references or [], search_provider)
        
        # 3. Load and Fill Template
        with open(self.template_path, "r", encoding="utf-8") as f:
            template = f.read()
            
        final_html = template.replace("{{ content_html }}", content_html)
        final_html = final_html.replace("{{ timestamp }}", timestamp)
        final_html = final_html.replace("{{ suggestions }}", suggestions_html)
        final_html = final_html.replace("{{ stats }}", stats_html)
        final_html = final_html.replace("{{ references }}", references_html)
        final_html = final_html.replace("{{ response_header }}", response_header)
        final_html = final_html.replace("{{ references_json }}", json.dumps(references or []))
        
        # 4. Render with Playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            # Use device_scale_factor=2 for high DPI rendering (better quality)
            page = await browser.new_page(viewport={"width": 450, "height": 1200}, device_scale_factor=2)
            
            # Set content
            await page.set_content(final_html)
            
            # Force wait for all images to load and hide broken ones
            await page.evaluate("""
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
            """)
            
            # Wait for content to load/render
            await page.wait_for_load_state("networkidle")
            
            # Update Timing Stats
            if isinstance(stats, list):
                # Sum up time from all steps
                agent_time = sum(s.get("time", 0) for s in stats)
            else:
                agent_time = stats.get("time", 0) if stats else 0
            current_time = asyncio.get_event_loop().time()
            render_duration = current_time - render_start_time
            total_duration = agent_time + render_duration
            
            await page.evaluate(f"""
                () => {{
                    const renderEl = document.getElementById('render-time-display');
                    if (renderEl) renderEl.innerText = '{render_duration:.1f}s';
                    
                    const totalEl = document.getElementById('total-time-display');
                    if (totalEl) totalEl.innerText = '{total_duration:.1f}s';
                }}
            """)
            
            # Locate the container to take a screenshot of just the content
            element = await page.query_selector("#main-container")
            
            if element:
                await element.screenshot(path=output_path)
            else:
                # Fallback to full page if container not found (shouldn't happen)
                await page.screenshot(path=output_path, full_page=True)
                
            await browser.close()

    def _generate_model_card_html(self, index: int, model: Dict[str, Any]) -> str:
        name = model.get("name", "Unknown")
        provider = model.get("provider", "Unknown")
        icon_data = model.get("provider_icon", "")
        is_default = model.get("is_default", False)
        is_vision_default = model.get("is_vision_default", False)
        
        # Badges
        badges_html = ""
        if is_default:
            badges_html += '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-blue-50 text-blue-600 border border-blue-100">DEFAULT</span>'
        if is_vision_default:
            badges_html += '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-purple-50 text-purple-600 border border-purple-100">DEFAULT</span>'
        
        # Capability Badges
        if model.get("vision"):
            badges_html += '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-purple-50 text-purple-600 border border-purple-100">VISION</span>'
        if model.get("tools"):
            badges_html += '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-green-50 text-green-600 border border-green-100">TOOLS</span>'
        if model.get("online"):
            badges_html += '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-cyan-50 text-cyan-600 border border-cyan-100">ONLINE</span>'
        if model.get("reasoning"):
            badges_html += '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-orange-50 text-orange-600 border border-orange-100">REASONING</span>'

        # Icon
        icon_html = ""
        if icon_data:
            icon_html = f'<img src="{icon_data}" class="w-5 h-5 object-contain rounded-sm">'
        else:
            icon_html = '<span class="w-5 h-5 flex items-center justify-center text-xs">📦</span>'

        return f'''
        <div class="bg-white rounded-xl p-4 shadow-sm border border-gray-200 flex flex-col gap-2 transition-all hover:shadow-md">
            <div class="flex justify-between items-start">
                <div class="flex items-center gap-2">
                    <span class="flex items-center justify-center w-5 h-5 rounded bg-gray-100 text-gray-500 text-xs font-mono font-bold">{index}</span>
                    {icon_html}
                    <h3 class="font-bold text-gray-800 text-sm">{name}</h3>
                </div>
            </div>
            
            <div class="pl-7 flex flex-col gap-1.5">
                <div class="flex flex-wrap gap-1">
                    {badges_html}
                </div>
                <div class="flex items-center gap-1.5 text-xs text-gray-500">
                    <span class="font-mono text-gray-400">Provider:</span>
                    <div class="flex items-center gap-1.5 bg-gray-50 px-2 py-1 rounded border border-gray-100">
                        <span class="font-medium text-gray-700">{provider}</span>
                    </div>
                </div>
            </div>
        </div>
        '''

    async def render_models_list(self, models: List[Dict[str, Any]], output_path: str, default_base_url: str = "https://openrouter.ai/api/v1"):
        """
        Render the list of models to an image.
        """
        # Resolve template path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        plugin_root = os.path.dirname(current_dir)
        template_path = os.path.join(plugin_root, "assets", "models_template.html")
        
        # Generate HTML for models
        models_html_parts = []
        for i, m in enumerate(models, 1):
            m_copy = m.copy()
            if not m_copy.get("provider"):
                url = m_copy.get("base_url")
                if not url:
                    url = default_base_url
                
                m_copy["provider"] = self._get_domain(url)
            
            # Determine provider icon
            icon_name = m_copy.get("icon")
            if icon_name:
                icon_name = icon_name.lower()
            
            if not icon_name:
                provider = m_copy.get("provider", "")
                
                if "." in provider:
                    # Fallback: strip TLD
                    parts = provider.split(".")
                    if len(parts) >= 2:
                        icon_name = parts[-2]
                    else:
                        icon_name = provider
                else:
                    icon_name = provider
            
            # Get icon data URL
            m_copy["provider_icon"] = self._get_icon_data_url(icon_name or "openai")
            
            models_html_parts.append(self._generate_model_card_html(i, m_copy))
            
        models_html = "\n".join(models_html_parts)
        
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
            
        final_html = template.replace("{{ models_list }}", models_html)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 450, "height": 800}, device_scale_factor=2)
            
            await page.set_content(final_html)
            await page.wait_for_load_state("networkidle")
            
            element = await page.query_selector("#main-container")
            if element:
                await element.screenshot(path=output_path)
            else:
                await page.screenshot(path=output_path, full_page=True)
                
            await browser.close()
