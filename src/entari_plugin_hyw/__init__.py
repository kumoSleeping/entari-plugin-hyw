"""
entari-plugin-hyw - Entari Plugin for HYW

Use large language models to interpret chat messages.
"""

from dataclasses import dataclass, field
from importlib.metadata import version as get_version
from typing import List, Dict, Any, Optional
import time
import asyncio
import os
import secrets
import base64
import re
import tempfile

from arclet.alconna import Alconna, Args, AllParam, Arparma
from arclet.entari import metadata, listen, Session, plugin_config, BasicConfModel, command
from arclet.entari import MessageChain, Text, Image, MessageCreatedEvent, Quote, At
from satori.element import Custom
from loguru import logger
from arclet.entari.event.command import CommandReceive
from arclet.entari.event.lifespan import Cleanup

# Import from internal hyw_core
from hyw_core import HywCore, HywCoreConfig, QueryRequest
from hyw_core.browser_control import (
    ContentRenderer,
    get_content_renderer,
    set_global_renderer,
    close_screenshot_service,
)
from hyw_core.browser_control.manager import close_shared_browser

# Local modules
from .history import HistoryManager
from .misc import (
    process_onebot_json, 
    process_images, 
    resolve_model_name, 
    render_refuse_answer, 
    render_image_unsupported,
)
from .search_cache import SearchResultCache, parse_single_index, parse_multi_indices


def parse_filter_syntax(query: str, max_count: int = 3):
    """
    Parse enhanced filter syntax supporting:
    - Chinese/English colons (: ：) and commas (, ，)
    - Multiple filters: "mcmod=2, github=1 : xxx" 
    - Index lists: "1, 2, 3 : xxx"
    - Max total selections
    
    Returns:
        (filters, search_query, error_msg)
        filters: list of (filter_type, filter_value, count) tuples
                 filter_type: 'index' or 'link'
                 count: how many to get (default 1)
        search_query: the actual search query
        error_msg: error message if exceeded max
    """
    if not query:
        return [], query, None
    
    # Normalize Chinese punctuation to English
    normalized = query.replace('：', ':').replace('，', ',').replace('、', ',')
    
    # Handle escaped colons: \: or /: -> placeholder
    normalized = re.sub(r'[/\\]:', '\x00COLON\x00', normalized)
    
    # Split by colon - last part is the search query
    parts = normalized.split(':')
    if len(parts) < 2:
        # No colon found, restore escaped colons and return as-is
        return [], query.replace('\\:', ':').replace('/:', ':'), None
    
    # Everything after the last colon is the search query
    search_query = parts[-1].strip().replace('\x00COLON\x00', ':')
    
    # Everything before is the filter specification
    filter_spec = ':'.join(parts[:-1]).strip().replace('\x00COLON\x00', ':')
    
    if not filter_spec or not search_query:
        return [], query.replace('\\:', ':').replace('/:', ':'), None
    
    # Parse filter specifications (comma-separated)
    filter_items = [f.strip() for f in filter_spec.split(',') if f.strip()]
    
    filters = []
    for item in filter_items:
        # Check for "filter=count" pattern (e.g., "mcmod=2")
        eq_match = re.match(r'^(\w+)\s*=\s*(\d+)$', item)
        if eq_match:
            filter_name = eq_match.group(1).lower()
            count = int(eq_match.group(2))
            filters.append(('link', filter_name, count))
        elif item.isdigit():
            # Pure index
            filters.append(('index', int(item), 1))
        else:
            # Filter name without count (default count=1)
            filters.append(('link', item.lower(), 1))
    
    # Calculate total count
    total = sum(f[2] for f in filters)
    if total > max_count:
        return [], search_query, f"最多选择{max_count}个结果 (当前选择了{total}个)"
    
    return filters, search_query, None


try:
    __version__ = get_version("entari_plugin_hyw")
except Exception:
    __version__ = "4.0.0-rc8"


def parse_color(color: str) -> str:
    if not color:
        return "#ef4444"
    color = str(color).strip()
    if color.startswith('#') and len(color) in [4, 7]:
        return color
    if re.match(r'^[0-9a-fA-F]{6}$', color):
        return f'#{color}'
    rgb_match = re.match(r'^\(?(\d+)[,\s]+(\d+)[,\s]+(\d+)\)?$', color)
    if rgb_match:
        r, g, b = (max(0, min(255, int(x))) for x in rgb_match.groups())
        return f'#{r:02x}{g:02x}{b:02x}'
    return "#ef4444"


class _RecentEventDeduper:
    def __init__(self, ttl_seconds: float = 30.0, max_size: int = 2048):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._seen: Dict[str, float] = {}

    def seen_recently(self, key: str) -> bool:
        now = time.time()
        if len(self._seen) > self.max_size:
            self._prune(now)
        ts = self._seen.get(key)
        if ts is None or now - ts > self.ttl_seconds:
            self._seen[key] = now
            return False
        return True

    def _prune(self, now: float):
        expired = [k for k, ts in self._seen.items() if now - ts > self.ttl_seconds]
        for k in expired:
            self._seen.pop(k, None)

_event_deduper = _RecentEventDeduper()


@dataclass
class HywConfig(BasicConfModel):
    """Plugin configuration"""
    admins: List[str] = field(default_factory=list)
    models: List[Dict[str, Any]] = field(default_factory=list)
    question_command: str = "/q"
    language: str = "Simplified Chinese"
    temperature: float = 0.4
    
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    
    search_engine: str = "google"
    
    headless: bool = False
    save_conversation: bool = False
    reaction: bool = False
    quote: bool = False
    theme_color: str = "#ff0000"

    # Nested configurations
    main: Optional[Dict[str, Any]] = None
    instruct: Optional[Dict[str, Any]] = None
    vision: Optional[Dict[str, Any]] = None
    deepsearch_instruct: Optional[Dict[str, Any]] = None
    deepsearch_agent: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        self.theme_color = parse_color(self.theme_color)
    
    def to_hyw_core_config(self) -> HywCoreConfig:
        main_cfg = self.main or {}
        instruct_cfg = self.instruct or {}
        
        return HywCoreConfig.from_dict({
            "models": self.models,
            "model_name": self.model_name or "",
            "api_key": self.api_key or "",
            "base_url": self.base_url,
            "temperature": self.temperature,
            "search_engine": self.search_engine,
            "headless": self.headless,
            "language": self.language,
            "theme_color": self.theme_color,
            
            # Map nested 'main' config to summary stage
            "summary_model": main_cfg.get("model_name"),
            "summary_api_key": main_cfg.get("api_key"),
            "summary_base_url": main_cfg.get("base_url"),
            "summary_extra_body": main_cfg.get("extra_body"),
            
            # Map nested 'instruct' config to instruct stage
            "instruct_model": instruct_cfg.get("model_name"),
            "instruct_api_key": instruct_cfg.get("api_key"),
            "instruct_base_url": instruct_cfg.get("base_url"),
            "instruct_extra_body": instruct_cfg.get("extra_body"),
        })
    
    def get_model_config(self, stage: str) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
        }


conf = plugin_config(HywConfig)
history_manager = HistoryManager()
renderer = ContentRenderer(headless=conf.headless)
set_global_renderer(renderer)
search_cache = SearchResultCache(ttl_seconds=600.0)  # 10 minutes

_hyw_core: Optional[HywCore] = None

def get_hyw_core() -> HywCore:
    global _hyw_core
    if _hyw_core is None:
        _hyw_core = HywCore(conf.to_hyw_core_config())
    return _hyw_core


@listen(Cleanup)
async def cleanup_screenshot_service():
    global _hyw_core
    try:
        if _hyw_core:
            await _hyw_core.close()
            _hyw_core = None
        await close_screenshot_service()
        close_shared_browser()
    except Exception as e:
        logger.warning(f"Failed to cleanup: {e}")


async def react(session: Session, emoji: str):
    if not conf.reaction: return
    try:
        await session.reaction_create(emoji=emoji)
    except Exception as e:
        logger.warning(f"Reaction failed: {e}")


async def process_request(
    session: Session[MessageCreatedEvent],
    all_param: Optional[MessageChain] = None,
    selected_model: Optional[str] = None,
) -> None:
    mc = MessageChain(all_param)
    if session.reply:
        try:
            reply_msg_id = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
            if not (reply_msg_id and history_manager.is_bot_message(reply_msg_id)):
                mc.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception:
            mc.extend(MessageChain(" ") + session.reply.origin.message)
    
    filtered = mc.get(Text) + mc.get(Image) + mc.get(Custom)
    mc = MessageChain(filtered)
    
    text_content = str(mc.get(Text)).strip()
    text_content = re.sub(r'<img[^>]+>', '', text_content, flags=re.IGNORECASE)
    
    if not text_content and not mc.get(Image) and not mc.get(Custom):
        return

    hist_key = None
    if session.reply and hasattr(session.reply.origin, 'id'):
        hist_key = history_manager.get_conversation_id(str(session.reply.origin.id))
    
    hist_payload = history_manager.get_history(hist_key) if hist_key else []
    context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"

    if conf.reaction: await react(session, "✨")

    try:
        msg_text = str(mc.get(Text)).strip() if mc.get(Text) else ""
        msg_text = re.sub(r'<img[^>]+>', '', msg_text, flags=re.IGNORECASE)
        
        if not msg_text and (mc.get(Image) or mc.get(Custom)):
            msg_text = "[图片]"
        
        for custom in [e for e in mc if isinstance(e, Custom)]:
            if custom.tag == 'onebot:json':
                if decoded := process_onebot_json(custom.attributes()): 
                    msg_text += f"\n{decoded}"
                break
        
        model = selected_model
        if model:
            resolved, _ = resolve_model_name(model, conf.models)
            if resolved:
                model = resolved

        images, _ = await process_images(mc, None)
        
        # Prepare renderer
        local_renderer = await get_content_renderer()
        render_tab_task = asyncio.create_task(local_renderer.prepare_tab())

        async def send_noti(msg: str):
            try:
                if conf.quote:
                    await session.send([Quote(session.event.message.id), msg])
                else:
                    await session.send(msg)
            except Exception as e:
                logger.warning(f"Failed to send notification: {e}")

        request = QueryRequest(
            user_input=msg_text,
            images=images,
            conversation_history=hist_payload,
            model_name=model,
            send_notification=send_noti
        )
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name
        
        core = get_hyw_core()
        # 1. Query ONLY (no render path provided)
        # Pass output_path=None so it returns raw response without internal rendering
        response = await core.query(request, output_path=None)
        
        # 2. Get the warmed-up tab
        try:
            tab_id = await render_tab_task
        except Exception:
            tab_id = None
        
        display_session_id = history_manager.generate_short_code()

        if response.should_refuse:
            render_ok = await render_refuse_answer(
                renderer=local_renderer,
                output_path=output_path,
                reason=response.refuse_reason or 'Refused',
                theme_color=conf.theme_color,
                tab_id=tab_id,
            )
        elif not response.success:
            await session.send(f"Error: {response.error}")
            return
        else:
            # 3. Explicit External Render using the Parallel Tab
            render_ok = await core.render(
                markdown_content=response.content,
                output_path=output_path,
                stats={"total_time": response.total_time},
                references=response.references,
                page_references=response.page_references,
                image_references=response.image_references,
                stages_used=response.stages_used,
                tab_id=tab_id
            )
            if render_ok:
                response.image_path = output_path
        
        if render_ok:
            with open(output_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            
            msg_chain = MessageChain(Image(src=f'data:image/png;base64,{img_data}'))
            if conf.quote:
                msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
            
            sent = await session.send(msg_chain)
            
            sent_id = next((str(e.id) for e in sent if hasattr(e, 'id')), None) if sent else None
            msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)
            
            updated_history = hist_payload + [
                {"role": "user", "content": msg_text},
                {"role": "assistant", "content": response.content}
            ]
            
            # Save to Memory
            history_manager.remember(
                sent_id, updated_history, [msg_id],
                {"model": model}, context_id, code=display_session_id,
            )
            
            # Save to Disk (Debug/Logging)
            if conf.save_conversation:
                # Extract traces from response
                trace = response.stages_trace
                instruct_traces = trace.get("instruct_rounds") if trace else None
                
                # Check for web_results in response (needs Core update)
                web_results = getattr(response, "web_results", [])
                
                history_manager.save_to_disk(
                    key=sent_id, 
                    image_path=output_path,
                    web_results=web_results,
                    instruct_traces=instruct_traces,
                    vision_trace=None # Vision integrated into Instruct now
                )
        
        if os.path.exists(output_path):
            os.remove(output_path)

    except Exception as e:
        logger.exception(f"Error: {e}")
        await session.send(f"Error: {e}")



alc = Alconna(conf.question_command, Args["all_param;?", AllParam])

@command.on(alc)    
async def handle_question_command(session: Session[MessageCreatedEvent], result: Arparma):
    try:
        mid = str(session.event.message.id) if getattr(session.event, "message", None) else str(session.event.id)
        dedupe_key = f"{getattr(session.account, 'id', 'account')}:{mid}"
        if _event_deduper.seen_recently(dedupe_key):
            return
    except Exception:
        pass
    
    args = result.all_matched_args
    all_param = args.get("all_param")
    
    # Extract query text
    if all_param:
        if isinstance(all_param, MessageChain):
            query_text = str(all_param.get(Text)).strip()
        else:
            query_text = str(all_param).strip()
    else:
        query_text = ""
    
    # Check if replying to a cached search result
    reply_msg_id = None
    if session.reply and hasattr(session.reply.origin, 'id'):
        reply_msg_id = str(session.reply.origin.id)
    
    # Quote mode: Use cached search results
    if reply_msg_id:
        cached = search_cache.get(reply_msg_id)
        if cached:
            # Parse indices if provided
            indices = parse_multi_indices(query_text, max_count=3) if query_text else None
            
            # Check if too many indices requested (parse_multi_indices returns None if > max_count)
            if query_text and indices is None:
                # Check if it looks like indices but exceeded limit
                import re
                if re.match(r'^[\d,、\s\-–]+$', query_text):
                    await session.send("最多选择3个结果进行总结")
                    search_cache.cleanup()
                    return
            
            if conf.reaction:
                asyncio.create_task(react(session, "✨"))
            
            core = get_hyw_core()
            local_renderer = await get_content_renderer()
            tab_task = asyncio.create_task(local_renderer.prepare_tab())
            
            # Collect screenshots for selected pages
            screenshots = []
            if indices:
                # Screenshot mode: capture pages for selected indices
                for idx in indices:
                    if idx < len(cached.results):
                        url = cached.results[idx].get("url", "")
                        if url:
                            b64_img = await core.screenshot(url)
                            if b64_img:
                                screenshots.append(b64_img)
                
                if not screenshots:
                    try: await tab_task
                    except: pass
                    await session.send("无法截图所选页面")
                    search_cache.cleanup()
                    return
                
                user_query = f"总结关于 \"{cached.query}\" 的内容"
            else:
                # No indices - summarize based on cached snippets (no screenshots)
                context_parts = []
                for i, res in enumerate(cached.results[:10]):
                    title = res.get("title", f"Result {i+1}")
                    snippet = res.get("content", "") or res.get("snippet", "")
                    context_parts.append(f"## {title}\n{snippet}")
                
                context_message = f"基于搜索 \"{cached.query}\" 的结果摘要回答用户问题:\n\n" + "\n\n".join(context_parts)
                user_query = query_text if query_text else f"总结关于 \"{cached.query}\" 的搜索结果"
            
            # Build request with screenshots (if any)
            if screenshots:
                request = QueryRequest(
                    user_input=user_query,
                    images=screenshots,
                    conversation_history=[],
                    model_name=None,
                )
            else:
                request = QueryRequest(
                    user_input=f"{context_message}\n\n用户问题: {user_query}",
                    images=[],
                    conversation_history=[],
                    model_name=None,
                )
            
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                output_path = tf.name
            
            response = await core.query(request, output_path=None)
            
            try:
                tab_id = await tab_task
            except Exception:
                tab_id = None
            
            if response.success and response.content:
                render_ok = await core.render(
                    markdown_content=response.content,
                    output_path=output_path,
                    stats={"total_time": response.total_time},
                    references=[],
                    page_references=[],
                    tab_id=tab_id
                )
                
                if render_ok and os.path.exists(output_path):
                    with open(output_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode()
                    
                    msg_chain = MessageChain(Image(src=f'data:image/png;base64,{img_data}'))
                    if conf.quote:
                        msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
                    
                    await session.send(msg_chain)
                    os.remove(output_path)
                else:
                    await session.send(response.content[:500])
            else:
                await session.send(f"总结失败: {response.error or 'Unknown error'}")
            
            search_cache.cleanup()
            return
    
    # === Filter Mode: Search + Find matching links + Summarize ===
    filters, search_query, filter_error = parse_filter_syntax(query_text, max_count=3)
    
    if filter_error:
        await session.send(filter_error)
        return
    
    if filters:
        if conf.reaction:
            asyncio.create_task(react(session, "✨"))
        
        core = get_hyw_core()
        local_renderer = await get_content_renderer()
        
        # Run search and prepare tab in parallel
        search_task = asyncio.create_task(core.search([search_query]))
        tab_task = asyncio.create_task(local_renderer.prepare_tab())
        
        results = await search_task
        flat_results = results[0] if results else []
        
        if not flat_results:
            try: await tab_task
            except: pass
            await session.send("Search returned no results.")
            return
        
        visible = [r for r in flat_results if not r.get("_hidden", False)]
        
        # Collect URLs to screenshot
        urls_to_screenshot = []
        for filter_type, filter_value, count in filters:
            if filter_type == 'index':
                idx = filter_value - 1
                if 0 <= idx < len(visible):
                    url = visible[idx].get("url", "")
                    if url and url not in urls_to_screenshot:
                        urls_to_screenshot.append(url)
                else:
                    try: await tab_task
                    except: pass
                    await session.send(f"序号 {filter_value} 超出范围 (1-{len(visible)})")
                    return
            else:
                found_count = 0
                for res in visible:
                    url = res.get("url", "")
                    if filter_value in url.lower() and url not in urls_to_screenshot:
                        urls_to_screenshot.append(url)
                        found_count += 1
                        if found_count >= count:
                            break
                
                if found_count == 0:
                    try: await tab_task
                    except: pass
                    await session.send(f"未找到包含 \"{filter_value}\" 的链接")
                    return
        
        if not urls_to_screenshot:
            try: await tab_task
            except: pass
            await session.send("未找到匹配的链接")
            return
        
        # Take screenshots concurrently
        screenshot_tasks = [core.screenshot(url) for url in urls_to_screenshot]
        screenshot_results = await asyncio.gather(*screenshot_tasks)
        screenshots = [b64 for b64 in screenshot_results if b64]
        
        if not screenshots:
            try: await tab_task
            except: pass
            await session.send("无法截图页面")
            return
        
        # Pass screenshots to LLM for summarization
        user_query = f"总结关于 \"{search_query}\" 的内容"
        
        request = QueryRequest(
            user_input=user_query,
            images=screenshots,
            conversation_history=[],
            model_name=None,
        )
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name
        
        response = await core.query(request, output_path=None)
        
        try:
            tab_id = await tab_task
        except Exception:
            tab_id = None
        
        if response.success and response.content:
            render_ok = await core.render(
                markdown_content=response.content,
                output_path=output_path,
                stats={"total_time": response.total_time},
                references=[],
                page_references=[],
                tab_id=tab_id
            )
            
            if render_ok and os.path.exists(output_path):
                with open(output_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode()
                
                msg_chain = MessageChain(Image(src=f'data:image/png;base64,{img_data}'))
                if conf.quote:
                    msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
                
                await session.send(msg_chain)
                os.remove(output_path)
            else:
                await session.send(response.content[:500])
        else:
            await session.send(f"总结失败: {response.error or 'Unknown error'}")
        
        return
    
    # Normal query mode (no cache context)
    await process_request(session, all_param)


# Search/Web Command (/w)
alc_search = Alconna("/w", Args["query;?", AllParam])

@command.on(alc_search)
async def handle_web_command(session: Session[MessageCreatedEvent], result: Arparma):
    """
    Handle web command /w:
    - If query is index + Quote -> Screenshot cached result
    - If query is URL -> Screenshot
    - If query is text -> Search
    """
    query = result.all_matched_args.get("query")
    
    # Extract query text
    if query:
        if isinstance(query, MessageChain):
            query = str(query.get(Text)).strip()
        query = str(query).strip()
    else:
        query = ""
    
    # Check if replying to a cached search result
    reply_msg_id = None
    if session.reply and hasattr(session.reply.origin, 'id'):
        reply_msg_id = str(session.reply.origin.id)
    
    # Quote + Index mode: Screenshot specific cached result
    if reply_msg_id:
        cached = search_cache.get(reply_msg_id)
        if cached:
            # Parse index from query
            idx = parse_single_index(query)
            if idx is None:
                # No valid index - show prompt
                await session.send("请指定序号 (1-10)")
                search_cache.cleanup()  # Lazy cleanup
                return
            
            if idx >= len(cached.results):
                await session.send(f"序号超出范围 (1-{len(cached.results)})")
                search_cache.cleanup()
                return
            
            # Screenshot the cached URL
            target_result = cached.results[idx]
            target_url = target_result.get("url", "")
            if not target_url:
                await session.send("该结果无有效URL")
                search_cache.cleanup()
                return
            
            if conf.reaction:
                asyncio.create_task(react(session, "📸"))
            
            core = get_hyw_core()
            b64_img = await core.screenshot(target_url)
            
            if b64_img:
                msg_chain = MessageChain(Image(src=f'data:image/jpeg;base64,{b64_img}'))
                if conf.quote:
                    msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
                await session.send(msg_chain)
            else:
                await session.send(f"截图失败: {target_url}")
            
            search_cache.cleanup()
            return
    
    # No query and no cache context - nothing to do
    if not query:
        return

    try:
        core = get_hyw_core()
        
        # 1. URL Detection
        url_pattern = re.compile(r'^https?://(?:[-\w./?=&%#]+)')
        if url_pattern.match(query):
            # === URL Screenshot Mode ===
            if conf.reaction: asyncio.create_task(react(session, "📸"))
            
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                output_path = tf.name

            b64_img = await core.screenshot(query)
            
            if b64_img:
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(b64_img))
                    
                msg_chain = MessageChain(Image(src=f'data:image/jpeg;base64,{b64_img}'))
                if conf.quote:
                    msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
                
                await session.send(msg_chain)
                
                if conf.save_conversation:
                    mid = str(session.event.message.id) if getattr(session.event, "message", None) else str(session.event.id)
                    context_id = f"guild_{session.guild.id}" if session.guild else "user"
                    history_manager.remember(mid, [{"role": "user", "content": f"/w {query}"}], [], {}, context_id=context_id)
                    history_manager.save_to_disk(mid, image_path=output_path, web_results=[{"url": query, "title": "Screenshot", "_type": "screenshot"}])

                os.remove(output_path)
            else:
                await session.send(f"Failed to screenshot URL: {query}")
            return

        # 2. Search Mode (Fallthrough)
        
        # Parse enhanced filter syntax
        filters, search_query, filter_error = parse_filter_syntax(query, max_count=3)
        
        if filter_error:
            await session.send(filter_error)
            return
        
        # Search first
        search_task = asyncio.create_task(core.search([search_query]))
        
        if conf.reaction: 
            asyncio.create_task(react(session, "🔍"))
        
        results = await search_task
        flat_results = results[0] if results else []
        
        if not flat_results:
            await session.send("Search returned no results.")
            return

        visible = [r for r in flat_results if not r.get("_hidden", False)]
        
        if not visible:
            await session.send("Search returned no visible results.")
            return
        
        # === Filter Mode: Screenshot matching links ===
        if filters:
            urls_to_screenshot = []
            
            for filter_type, filter_value, count in filters:
                if filter_type == 'index':
                    # Index-based (1-based)
                    idx = filter_value - 1
                    if 0 <= idx < len(visible):
                        url = visible[idx].get("url", "")
                        if url and url not in urls_to_screenshot:
                            urls_to_screenshot.append(url)
                    else:
                        await session.send(f"序号 {filter_value} 超出范围 (1-{len(visible)})")
                        return
                else:
                    # Link filter: find URLs containing filter term
                    found_count = 0
                    for res in visible:
                        url = res.get("url", "")
                        if filter_value in url.lower() and url not in urls_to_screenshot:
                            urls_to_screenshot.append(url)
                            found_count += 1
                            if found_count >= count:
                                break
                    
                    if found_count == 0:
                        await session.send(f"未找到包含 \"{filter_value}\" 的链接")
                        return
            
            if not urls_to_screenshot:
                await session.send("未找到匹配的链接")
                return
            
            if conf.reaction:
                asyncio.create_task(react(session, "📸"))
            
            # Take screenshots concurrently
            screenshot_tasks = [core.screenshot(url) for url in urls_to_screenshot]
            screenshot_results = await asyncio.gather(*screenshot_tasks)
            
            images = [Image(src=f'data:image/jpeg;base64,{b64}') for b64 in screenshot_results if b64]
            
            if images:
                msg_chain = MessageChain(images)
                if conf.quote:
                    msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
                await session.send(msg_chain)
                
                if conf.save_conversation:
                    mid = str(session.event.message.id) if getattr(session.event, "message", None) else str(session.event.id)
                    context_id = f"guild_{session.guild.id}" if session.guild else "user"
                    history_manager.remember(mid, [{"role": "user", "content": f"/w {query}"}], [], {}, context_id=context_id)
            else:
                await session.send("截图失败")
            return
             
        # === Normal Search Mode: Screenshot search results page ===
        search_service = core._search_service
        search_url = search_service._build_search_url(search_query)
        
        # Handle address bar search marker
        if search_url.startswith("__ADDRESS_BAR_SEARCH__:"):
            import urllib.parse
            encoded_query = urllib.parse.quote_plus(search_query)
            search_url = f"https://www.google.com/search?q={encoded_query}"
        
        b64_img = await core.screenshot(search_url)
        
        if b64_img:
            msg_chain = MessageChain(Image(src=f'data:image/jpeg;base64,{b64_img}'))
            if conf.quote:
                msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
            
            sent = await session.send(msg_chain)
            
            # Store in cache for future /w and /q lookups
            sent_id = next((str(e.id) for e in sent if hasattr(e, 'id')), None) if sent else None
            if sent_id:
                search_cache.store(sent_id, visible[:10], search_query)
            
            if conf.save_conversation:
                mid = str(session.event.message.id) if getattr(session.event, "message", None) else str(session.event.id)
                context_id = f"guild_{session.guild.id}" if session.guild else "user"
                history_manager.remember(mid, [{"role": "user", "content": f"/w {query}"}], [], {}, context_id=context_id)
        else:
            await session.send(f"截图搜索页面失败: {search_url}")
        
        search_cache.cleanup()  # Lazy cleanup

    except Exception as e:
        logger.error(f"Search command failed: {e}")
        await session.send(f"Search error: {e}")


metadata("hyw", author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}], version=__version__, config=HywConfig)

@listen(CommandReceive)
async def remove_at(content: MessageChain):
    return content.lstrip(At)
