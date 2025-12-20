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

    async def _set_content_safe(self, page, html: str, timeout_ms: int) -> bool:
        html_size = len(html)
        try:
            await page.set_content(html, wait_until="domcontentloaded", timeout=timeout_ms)
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

    def _generate_card_header(self, title: str, icon_data_url: str = None, icon_emoji: str = None, custom_icon_html: str = None, badge_text: str = None, badge_class: str = "llm", provider_text: str = None, behavior_summary: str = None, is_plain: bool = False, icon_box_class: str = None) -> str:
        # LLM Icon
        icon_html = ""
        if custom_icon_html:
            icon_html = custom_icon_html
        elif icon_data_url:
            icon_html = f'<img src="{icon_data_url}" class="w-8 h-8 object-contain rounded-md">'
        elif icon_emoji:
             icon_html = f'<span class="text-2xl shrink-0">{icon_emoji}</span>'
        elif badge_text: # Fallback icon based on badge type if no image
             icon_symbol = "🔎" if "search" in badge_class else "🤖"
             icon_html = f'<span class="text-2xl shrink-0">{icon_symbol}</span>'

        # Text Content
        # MC Advancement Style:
        # Title (Model Name)
        # Description (Provider • Behavior)
        
        main_text_color = "text-pink-600"
        sub_text_color = "text-gray-500"
        
        if is_plain:
             # For suggestions/MCP, usually simpler
             # Use the same layout but maybe smaller?
             # existing is_plain used for title color text-gray-900.
             pass

        # Restore behavior_display definition
        behavior_display = behavior_summary if behavior_summary is not None else "Text Generation"
        
        # If is_plain (like Suggestions/MCP), we usually just have a title. 
        # But if we want to reuse this for model card, we need the subtitle.
        
        subtitle_html = ""
        if not is_plain:
            if provider_text and provider_text.lower() not in ("unknown", "unknown provider"):
                subtitle_html = f'<div class="text-xs {sub_text_color} font-medium">{provider_text} &bull; {behavior_display}</div>'
            else:
                 subtitle_html = f'<div class="text-xs {sub_text_color} font-medium">{behavior_display}</div>'
        
        # Default icon box class if not provided
        if not icon_box_class:
            icon_box_class = "bg-gray-50 rounded-lg border border-gray-100"

        return f'''
        <div class="flex items-center gap-3 pb-3 mb-3 border-b border-gray-100 { '!bg-transparent !border-none !p-0 !mb-0 !pb-0' if is_plain else '' }">
            <div class="flex items-center justify-center w-10 h-10 {icon_box_class} shrink-0">
                {icon_html}
            </div>
            <div class="flex flex-col min-w-0">
                <div class="text-sm font-bold {main_text_color} uppercase tracking-wide whitespace-nowrap overflow-hidden text-ellipsis">{title}</div>
                {subtitle_html}
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

    def _generate_status_footer(self, stats: Union[Dict[str, Any], List[Dict[str, Any]]], billing_info: Dict[str, Any] = None) -> str:
        if not stats:
            return ""
            
        # Check if multi-step
        is_multi_step = isinstance(stats, list)
        
        # Billing HTML
        billing_html = ""
        if billing_info:
            total_cost = billing_info.get("total_cost", 0)
            if total_cost > 0:
                cost_cents = total_cost * 100
                cost_str = f"{cost_cents:.4f}¢"
                billing_html = f'''
                <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                    <span class="w-2 h-2 rounded-full bg-pink-500"></span>
                    <span>{cost_str}</span>
                </div>
                '''
        
        if is_multi_step:
            # Multi-step Layout
            step1_stats = stats[0]
            step2_stats = stats[1] if len(stats) > 1 else {}
            
            step1_time = step1_stats.get("time", 0)
            step2_time = step2_stats.get("time", 0)
            
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
            
            return f'''
            <div class="flex flex-col gap-2 bg-[#f2f2f2] rounded-2xl p-3 overflow-hidden">
                <div class="flex flex-wrap items-center gap-2 text-[10px] text-gray-600 font-bold font-mono uppercase tracking-wide">
                    {step1_html}
                    {step2_html}
                    {billing_html}
                </div>
            </div>
            '''
            
        else:
            # Single Step Layout
            agent_total_time = stats.get("time", 0)
            vision_time = stats.get("vision_duration", 0)
            llm_time = max(0, agent_total_time - vision_time)
            
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
                    {billing_html}
                </div>
            </div>
            '''
            
            total_html = f'''
            <div class="flex items-center gap-1.5 bg-white/60 px-2 py-1 rounded shadow-sm">
                <span class="w-2 h-2 rounded-full bg-gray-400"></span>
                <span id="total-time-display">...</span>
            </div>
            '''
            
            return f'''
            <div class="flex flex-col gap-2 bg-[#f2f2f2] rounded-2xl p-3 overflow-hidden">
                <div class="flex flex-wrap items-center gap-2 text-[10px] text-gray-600 font-bold font-mono uppercase tracking-wide">
                    {vision_html}
                    {agent_html}
                    {render_html}
                    {total_html}
                    {billing_html}
                </div>
            </div>
            '''

    def _render_list_card(
        self,
        icon_html: str,
        title_html: str, # Allow HTML for complex titles (like stage name + model)
        subtitle_html: str = None,
        link_url: str = None,
        right_content_html: str = None,
        is_link: bool = False,
        icon_box_class: str = "bg-gray-50 rounded-md shrink-0 ring-1 ring-inset ring-black/5",
        is_compact: bool = False
    ) -> str:
        tag = "a" if link_url else "div"
        href_attr = f'href="{link_url}" target="_blank"' if link_url else ""
        hover_class = "transition-colors hover:bg-gray-50" if (link_url or is_link) else ""
        
        # Compact Logic
        padding_class = "px-4 py-3.5"
        align_class = "items-start"
        icon_size_class = "w-8 h-8"
        
        if is_compact:
            padding_class = "p-2.5"
            align_class = "items-center"
            icon_size_class = "w-6 h-6"

        if icon_box_class and "w-" in icon_box_class and "h-" in icon_box_class:
             # If custom class provides size, don't override unless necessary.
             # Actually, just trust the caller's class if they provide one?
             # For now, let's append icon_size_class ONLY if icon_box_class is default
             pass 
        else:
             # If default class was passed (or user passed class without size), we might want to enforce size?
             # But the default argument includes size? No, default arg is "bg-gray-50...".
             # Wait, default arg doesn't include w-8 h-8.
             # The old code had: class="flex items-center justify-center w-8 h-8 ...
             pass

        # Consistent Card Style
        return f'''
        <{tag} {href_attr} class="flex {align_class} gap-3 {padding_class} rounded-lg border border-gray-100 bg-white shadow-sm no-underline text-inherit {hover_class}">
            <div class="flex items-center justify-center {icon_size_class} {icon_box_class}">
                {icon_html}
            </div>
            <div class="flex flex-col flex-1 min-w-0 gap-0.5">
                <div class="flex items-center gap-2 leading-tight min-w-0">
                    {title_html}
                </div>
                {f'<div>{subtitle_html}</div>' if subtitle_html else ''}
            </div>
            {f'<div class="shrink-0 ml-2">{right_content_html}</div>' if right_content_html else ''}
        </{tag}>
        '''



    def _generate_pipeline_html(self, stages: List[Dict[str, Any]], mcp_steps: List[Dict[str, Any]] = None, references: List[Dict[str, Any]] = None) -> str:
        """Generate HTML for unified pipeline display containing Stages, MCP Flow, and References.
        """
        if not stages and not mcp_steps and not references:
            return ""
        
        html_parts = ['<div class="flex flex-col gap-1.5">']
        
        # --- 1. Pipeline Stages ---
        STAGE_COLORS = {
            "Vision": "bg-purple-50 text-purple-600 border-purple-100",
            "Instruct": "bg-blue-50 text-blue-600 border-blue-100",
            "Search": "bg-orange-50 text-orange-600 border-orange-100",
            "Agent": "bg-green-50 text-green-600 border-green-100",
        }
        
        if stages:
            for i, stage in enumerate(stages):
                name = stage.get("name", "Step")
                model = stage.get("model", "")
                icon_config = stage.get("icon_config", "")
                provider = stage.get("provider", "")
                
                # Get model icon using the existing method
                icon_data_url = self._get_icon_data_url(icon_config) if icon_config else ""
                
                # Extract short model name for display
                if "/" in model:
                    model_short = model.split("/")[-1]
                else:
                    model_short = model
                # Truncate if too long
                if len(model_short) > 25:
                    model_short = model_short[:23] + "…"
                
                # Build icon HTML
                if name == "Search":
                    # Use Pink Globe SVG for Search stage, consistent with References
                    icon_html = '''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5 text-pink-500"><path stroke-linecap="round" stroke-linejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S12 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S12 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418" /></svg>'''
                elif icon_data_url:
                    icon_html = f'<img src="{icon_data_url}" class="w-5 h-5 object-contain rounded">'
                else:
                    # Fallback
                    stage_emoji = {"Vision": "👁️", "Search": "🔍", "Agent": "✨", "Instruct": "📝"}
                    emoji = stage_emoji.get(name, "⚙️")
                    icon_html = f'<span class="text-sm">{emoji}</span>'
                
                # Define color
                color = STAGE_COLORS.get(name, "bg-gray-50 text-gray-600 border-gray-100")
                
                provider_html = ""
                if provider and provider.lower() not in ("unknown", "unknown provider", ""):
                    provider_html = f'<span class="text-[10px] text-gray-400 shrink-0 truncate max-w-[80px]">{provider}</span>'

                time_val = stage.get("time", 0)
                cost_val = stage.get("cost", 0.0)
                time_str = f"{time_val:.2f}s"
                cost_str = f"${cost_val:.6f}" if cost_val > 0 else "$0"
                if name == "Search": cost_str = "$0"

                stats_html = f'<div class="flex items-center gap-3 text-[11px] text-gray-500 font-mono mt-0.5"><span>{time_str}</span><span>{cost_str}</span></div>'

                icon_box_class = f"{color} rounded-md border shrink-0"

                title_html = f'''
                        <span class="text-[11px] font-bold uppercase text-pink-600 shrink-0">{name}</span>
                        <span class="text-[11px] font-medium text-gray-700 truncate min-w-0" title="{model}">{model_short}</span>
                        <span class="ml-auto">{provider_html}</span>
                '''
                
                html_parts.append(self._render_list_card(
                    icon_html=icon_html,
                    title_html=title_html,
                    subtitle_html=stats_html,
                    right_content_html=None,
                    is_compact=True,
                    icon_box_class=icon_box_class
                ))
        
        # --- 2. MCP Flow ---
        if mcp_steps:
             # Header with Separator logic (or just simple header)
             # Use the simple header from _generate_card_header, but maybe simplified margin?
             # Pink Terminal Icon
            mcp_icon_svg = '''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5 text-pink-500"><path stroke-linecap="round" stroke-linejoin="round" d="m6.75 7.5 3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0 0 21 18V6a2.25 2.25 0 0 0-2.25-2.25H5.25A2.25 2.25 0 0 0 3 6v12a2.25 2.25 0 0 0 2.25 2.25Z" /></svg>'''
            mcp_header = self._generate_card_header("MCP FLOW", badge_text=None, custom_icon_html=mcp_icon_svg, is_plain=True)
            # Add some spacing before section if there were stages
            if stages:
                html_parts.append('<div class="mt-2"></div>')
            html_parts.append(mcp_header)
            
            # SVG icons
            I_STYLE = "width:14px;height:14px"
            STEP_ICONS = {
                "navigate": f'''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" style="{I_STYLE}"><path stroke-linecap="round" stroke-linejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S12 3 12 3m0 18a9 9 0 0 1-9-9" /></svg>''',
                "snapshot": f'''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" style="{I_STYLE}"><path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574" /></svg>''',
                "click": f'''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" style="{I_STYLE}"><path stroke-linecap="round" stroke-linejoin="round" d="M15.042 21.672 13.684 16.6m0 0-2.51 2.225.569-9.47 5.227 7.917-3.286-.672" /></svg>''',
                "type": f'''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" style="{I_STYLE}"><path stroke-linecap="round" stroke-linejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652" /></svg>''',
                "code": f'''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" style="{I_STYLE}"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25" /></svg>''',
                "default": f'''<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" style="{I_STYLE}"><path stroke-linecap="round" stroke-linejoin="round" d="M11.42 15.17 17.25 21" /></svg>''',
            }

            for i, step in enumerate(mcp_steps):
                name = step.get("name", "unknown")
                desc = step.get("description", "")
                icon_key = step.get("icon", "").lower()
                
                icon_svg = STEP_ICONS.get(icon_key, STEP_ICONS["default"])
                
                icon_box_class = "rounded-md border border-pink-100 bg-pink-50 text-pink-600 shrink-0"
                title_html = f'<div class="text-[12px] font-semibold text-gray-800 font-mono">{name}</div>'
                subtitle_html = f'<div class="text-[10px] text-gray-400">{desc}</div>' if desc else None
                
                html_parts.append(self._render_list_card(
                    icon_html=icon_svg, 
                    title_html=title_html,
                    subtitle_html=subtitle_html,
                    is_compact=True,
                    icon_box_class=icon_box_class
                ))

        # --- 3. References ---
        if references:
            # Pink Globe SVG
            ref_icon_svg = '''
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5 text-pink-500">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S12 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S12 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418" />
            </svg>
            '''
            ref_header = self._generate_card_header("REFERENCES", badge_text=None, custom_icon_html=ref_icon_svg, is_plain=True)
            if stages or mcp_steps:
                html_parts.append('<div class="mt-2"></div>')
            html_parts.append(ref_header)
            
            # Limit to 8 references
            refs = references[:8]
            
            for i, ref in enumerate(refs):
                title = ref.get("title", "No Title")
                url = ref.get("url", "#")
                try:
                    domain = urlparse(url).netloc
                    if domain.startswith("www."): domain = domain[4:]
                except Exception:
                    domain = "unknown"
                    
                favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
                
                icon_box = f'<div class="w-full h-full flex items-center justify-center text-[10px] font-bold text-pink-600 bg-pink-50 rounded-md border border-pink-100">{i+1}</div>'
                title_html = f'<div class="text-[12px] font-medium text-gray-800 truncate" title="{title}">{title}</div>'
                
                subtitle_html = f'''
                <div class="flex items-center gap-1 text-[10px] text-gray-400">
                    <img src="{favicon_url}" class="w-3 h-3 rounded-sm opacity-60" onerror="this.style.display='none'">
                    <span>{domain}</span>
                </div>
                '''
                
                html_parts.append(self._render_list_card(
                    icon_html=icon_box,
                    title_html=title_html,
                    subtitle_html=subtitle_html,
                    link_url=url,
                    is_compact=True,
                    icon_box_class="bg-pink-50 border border-pink-100 rounded-md shrink-0 ring-0" # Override default ring
                ))

        html_parts.append('</div>')
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

        # 1. Prepare Template Variables
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Header for Response Card
        if "/" in model_name:
            model_display = model_name.split("/")[-1].upper()
        else:
            model_display = model_name.upper()
            
        icon_data_url = self._get_icon_data_url(icon_config)
        
        # provider_domain = self._get_domain(base_url)
        # We now use passed provider_name instead of strict domain inference
        
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
            
            # vision_provider_domain = self._get_domain(vision_base_url or base_url)
            # New behavior: we ignore explicit vision provider text in header, just merge logic if needed or just use behavior summary.
            # But the user might want to see the specific vision model name?
            # User request: "Model UI Name Service Provider + Behavior Summary"
            # It implies a SINGLE model card.
            # If vision is used, we usually care about the main model responding.
            # We can mention Vision in behavior summary.
        
        # New "Automated Pipeline" Header
        # Pink/White style
        flowchart_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-6 h-6 text-pink-600"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>'
        
        response_header = self._generate_card_header(
            title="Automated Pipeline",
            custom_icon_html=flowchart_icon,
            badge_text="PIPELINE", 
            provider_text="", 
            behavior_summary="", 
            is_plain=False,
            icon_box_class="bg-gray-50 rounded-lg border border-gray-100"
        )
        
        suggestions_html = self._generate_suggestions_html(suggestions or [])
        # Stats Footer REMOVED (now displayed in stages)
        stats_html = "" 
        # Pass search_provider to references generation
        mcp_steps_html = "" # self._generate_mcp_steps_html(mcp_steps or [])
        # Generate pipeline (stages + mcp + references) HTML
        pipeline_html = self._generate_pipeline_html(stages_used or [], mcp_steps or [], references or [])
        
        # 2. Render with Playwright
        max_attempts = 1  # No retry - if set_content fails, retrying won't help
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            content_html = None
            final_html = None
            template = None
            parts = None
            try:
                # Server-side Markdown Rendering
                content_html = markdown.markdown(
                    markdown_content.strip(), 
                    extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
                )
                
                # Post-process to style citation markers [1], [2]...
                parts = re.split(r'(<code.*?>.*?</code>)', content_html, flags=re.DOTALL)
                for i, part in enumerate(parts):
                    if not part.startswith('<code'):
                        parts[i] = re.sub(r'\[(\d+)\](?![^<]*>)', r'<span class="inline-flex items-center justify-center min-w-[16px] h-4 px-0.5 text-[10px] font-bold text-pink-600 bg-pink-50 border border-pink-100 rounded mx-0.5 align-middle">\1</span>', part)
                content_html = "".join(parts)
                
                # Load and Fill Template
                logger.info(f"Loading template from {self.template_path}")
                with open(self.template_path, "r", encoding="utf-8") as f:
                    template = f.read()
                logger.info(f"Template size: {len(template)} bytes, header: {template[:100]}")
                    
                # Inject all pre-compiled assets (CSS + JS)
                final_html = template
                injected_assets = []
                for key, content in self.assets.items():
                    # Regex to match {{ key }} allowing for whitespace even between braces
                    # Matches {{key}}, { { key } }, {{ key }}, etc.
                    pattern = r"\{\s*\{\s*" + re.escape(key) + r"\s*\}\s*\}"
                    match = re.search(pattern, final_html)
                    if match:
                        final_html = re.sub(pattern, lambda _: content, final_html)
                        injected_assets.append(f"{key}({len(content)})")
                    else:
                        logger.warning(f"Asset placeholder NOT FOUND: {key}, pattern: {pattern}")
                        # Debug: show first 500 chars of template to see placeholders
                        logger.debug(f"Template start: {template[:500]}")
                
                logger.info(f"Injected assets: {', '.join(injected_assets)}")
                
                final_html = final_html.replace("{{ content_html }}", content_html)
                final_html = final_html.replace("{{ timestamp }}", timestamp)
                final_html = final_html.replace("{{ suggestions }}", suggestions_html)
                final_html = final_html.replace("{{ stats }}", stats_html)
                final_html = final_html.replace("{{ references }}", "") # Removed
                final_html = final_html.replace("{{ mcp_steps }}", "") # Removed
                final_html = final_html.replace("{{ stages }}", pipeline_html)
                final_html = final_html.replace("{{ response_header }}", response_header)
                final_html = final_html.replace("{{ references_json }}", json.dumps(references or []))
            except MemoryError:
                last_exc = "memory"
                logger.warning(f"ContentRenderer: out of memory while building HTML (attempt {attempt}/{max_attempts})")
                continue
            except Exception as exc:
                last_exc = exc
                logger.warning(f"ContentRenderer: failed to build HTML (attempt {attempt}/{max_attempts}) ({exc})")
                continue
            
            try:
                logger.info("ContentRenderer: launching playwright...")
                async with async_playwright() as p:
                    logger.info("ContentRenderer: playwright context ready, launching browser...")
                    browser = await p.chromium.launch(headless=True)
                    try:
                        # Use device_scale_factor=2 for high DPI rendering (better quality)
                        page = await browser.new_page(viewport={"width": 450, "height": 1200}, device_scale_factor=2)
                        
                        logger.debug("ContentRenderer: page created, setting content...")
                        
                        # Set content (10s timeout to handle slow CDN loading)
                        set_ok = await self._set_content_safe(page, final_html, 10000)
                        if not set_ok or page.is_closed():
                            raise RuntimeError("set_content failed")
                        
                        logger.debug("ContentRenderer: content set, waiting for images...")
                        
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
                        
                        logger.debug("ContentRenderer: images done, updating stats...")
                        

                        
                        # Brief wait for layout to stabilize (CSS is pre-compiled)
                        await asyncio.sleep(0.1)
                        
                        logger.debug("ContentRenderer: taking screenshot...")
                        
                        # Try element screenshot first, fallback to full page
                        element = await page.query_selector("#main-container")
                        
                        try:
                            if element:
                                await element.screenshot(path=output_path)
                            else:
                                await page.screenshot(path=output_path, full_page=True)
                        except Exception as screenshot_exc:
                            # Fallback to full page screenshot if element screenshot fails
                            logger.warning(f"ContentRenderer: element screenshot failed ({screenshot_exc}), trying full page...")
                            await page.screenshot(path=output_path, full_page=True)
                        
                        logger.debug("ContentRenderer: screenshot done")
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
                template = None
                parts = None
                gc.collect()
                
        logger.error(f"ContentRenderer: render failed after {max_attempts} attempts ({last_exc})")
        return False

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

    async def render_models_list(self, models: List[Dict[str, Any]], output_path: str, default_base_url: str = "https://openrouter.ai/api/v1", render_timeout_ms: int = 6000):
        """
        Render the list of models to an image.
        """
        # Resolve template path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        plugin_root = os.path.dirname(current_dir)
        template_path = os.path.join(plugin_root, "assets", "template.html")
        
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
        
        # Inject all pre-compiled assets (CSS + JS)
        final_html = template
        for key, content in self.assets.items():
            final_html = final_html.replace("{{ " + key + " }}", content)
        final_html = final_html.replace("{{ models_list }}", models_html)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 450, "height": 800}, device_scale_factor=2)
            
            await self._set_content_safe(page, final_html, render_timeout_ms)
            
            element = await page.query_selector("#main-container")
            if element:
                await element.screenshot(path=output_path)
            else:
                await page.screenshot(path=output_path, full_page=True)
                
            await browser.close()
