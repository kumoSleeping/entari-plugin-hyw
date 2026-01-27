"""
entari-plugin-hyw - Entari Plugin for HYW

Use large language models to interpret chat messages.
"""

from dataclasses import dataclass, field
from importlib.metadata import version as get_version
from typing import List, Dict, Any, Optional
import asyncio
import os
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
    parse_color,
    RecentEventDeduper,
)
from .filters import parse_filter_syntax
from .search_cache import SearchResultCache, parse_single_index, parse_multi_indices


try:
    __version__ = get_version("entari_plugin_hyw")
except Exception:
    __version__ = "4.0.0-rc8"


_event_deduper = RecentEventDeduper()


class TaskManager:
    """Manages async tasks for cancellation"""
    def __init__(self):
        self.tasks: Dict[str, asyncio.Task] = {}
        self.cleanups: Dict[str, callable] = {}

    def register(self, msg_id: str, task: asyncio.Task, cleanup: Optional[callable] = None):
        self.tasks[msg_id] = task
        if cleanup:
            self.cleanups[msg_id] = cleanup

    def unregister(self, msg_id: str):
        self.tasks.pop(msg_id, None)
        self.cleanups.pop(msg_id, None)

    async def cancel(self, msg_id: str) -> bool:
        task = self.tasks.get(msg_id)
        if task and not task.done():
            task.cancel()

            # Run cleanup if available
            cleanup = self.cleanups.get(msg_id)
            if cleanup:
                try:
                    if asyncio.iscoroutinefunction(cleanup):
                        await cleanup()
                    else:
                        cleanup()
                except Exception as e:
                    logger.warning(f"Cleanup failed for task {msg_id}: {e}")

            self.unregister(msg_id)
            return True
        return False

_task_manager = TaskManager()


@dataclass
class HywConfig(BasicConfModel):
    """Plugin configuration"""
    admins: List[str] = field(default_factory=list)
    models: List[Dict[str, Any]] = field(default_factory=list)
    question_command: str = "/q"
    web_command: str = "/w"
    stop_command: str = "/x"
    help_command: str = "/h"
    language: str = "Simplified Chinese"
    temperature: float = 0.4
    
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    
    search_engine: str = "duckduckgo"
    
    headless: bool = False
    save_conversation: bool = False
    reaction: bool = False
    quote: bool = False
    theme_color: str = "#ff0000"

    # Main model configuration (used for summary/main LLM calls)
    main: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        self.theme_color = parse_color(self.theme_color)
    
    def to_hyw_core_config(self) -> HywCoreConfig:
        main_cfg = self.main or {}
        
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
        })


conf = plugin_config(HywConfig)
history_manager = HistoryManager()
renderer = ContentRenderer(headless=conf.headless)
set_global_renderer(renderer)
search_cache = SearchResultCache(ttl_seconds=600.0)  # 10 minutes

# Initialize HywCore immediately at plugin load time (not lazy)
# This avoids the 2s delay on first user request caused by AsyncOpenAI client creation
_hyw_core: HywCore = HywCore(conf.to_hyw_core_config())

def get_hyw_core() -> HywCore:
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

        # Register cleanup for this specific request's resources
        msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)

        async def cleanup_resources():
            try:
                # If tab task is still running, cancel it
                if not render_tab_task.done():
                    render_tab_task.cancel()
                else:
                    # If tab is ready, close it
                    try:
                        tab_id = render_tab_task.result()
                        if tab_id:
                            await local_renderer.close_tab(tab_id)
                    except:
                        pass
            except Exception as e:
                logger.warning(f"Resource cleanup failed: {e}")

        # Update task manager with cleanup callback
        if _task_manager.tasks.get(msg_id):
            _task_manager.cleanups[msg_id] = cleanup_resources

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
        # Use agent mode with tool-calling capability
        # Agent can autonomously call web_tool up to 2 times, with IM notifications
        response = await core.query_agent(request, output_path=None)
        
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

            # Store web results in search cache for continuous conversation context
            # This allows users to reply to this message and have the AI "remember" the search results
            if response.web_results and sent_id:
                search_cache.store(sent_id, response.web_results, f"Context for {msg_id}")

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

    # Check if replying to a cached search result (/w context summary)
    reply_msg_id = None
    if session.reply and hasattr(session.reply.origin, 'id'):
        reply_msg_id = str(session.reply.origin.id)

    if reply_msg_id:
        cached = search_cache.get(reply_msg_id)
        if cached:
            # Extract current user query
            if all_param:
                if isinstance(all_param, MessageChain):
                    current_query = str(all_param.get(Text)).strip()
                else:
                    current_query = str(all_param).strip()
            else:
                current_query = ""

            # If empty query, assume request for summary
            if not current_query:
                current_query = "请详细总结上述搜索结果"

            # Build full context from cached results
            context_parts = []
            for i, res in enumerate(cached.results):
                title = res.get("title", f"Result {i+1}")
                url = res.get("url", "")
                content = res.get("content", "") or res.get("snippet", "")
                context_parts.append(f"## [{i+1}] {title}\nURL: {url}\n\n{content}")

            full_context = "\n\n".join(context_parts)

            # Construct augmented prompt
            new_prompt = f"基于以下搜索结果回答问题:\n\n【搜索上下文】\nSearch Query: {cached.query}\n\n{full_context}\n\n【用户问题】\n{current_query}"

            # Use MessageChain with Text for compatibility
            # This injects the search context into the prompt while maintaining the 'reply' link in history
            all_param = MessageChain(Text(new_prompt))

            # Log for debug
            logger.info(f"Injecting search context from message {reply_msg_id} into query")

    # Normal query mode (Standard Agentic Chat)
    # Register task for cancellation
    msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)
    task = asyncio.create_task(process_request(session, all_param))

    # Define cleanup to close potential tabs (handled inside process_request but good to have backup)
    # process_request handles its own cleanup, but we need to track the task itself
    _task_manager.register(msg_id, task)

    try:
        await task
    except asyncio.CancelledError:
        logger.info(f"Task {msg_id} cancelled by user")
        await session.send("❌ 任务已停止")
    except Exception as e:
        logger.error(f"Task failed: {e}")
    finally:
        _task_manager.unregister(msg_id)


# Search/Web Command (/w)
alc_search = Alconna(conf.web_command, Args["query;?", AllParam])

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
        else:
            # Reply to a non-cached message: append reply content to query
            try:
                # session.reply.origin.message is a list, wrap it in MessageChain
                reply_msg = MessageChain(session.reply.origin.message)
                reply_content = str(reply_msg.get(Text)).strip()
                if reply_content:
                    query = f"{query} {reply_content}".strip() if query else reply_content
                    logger.info(f"/w appended reply content, new query: '{query}'")
            except Exception as e:
                logger.warning(f"/w failed to extract reply content: {e}")
    
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
        
        # Start search first
        local_renderer = await get_content_renderer()
        search_task = asyncio.create_task(core.search([search_query]))
        
        # Only pre-warm tab if NOT in filter mode (filter mode = screenshots only, no card render)
        tab_task = None
        if not filters:
            tab_task = asyncio.create_task(local_renderer.prepare_tab())
        
        if conf.reaction: 
            asyncio.create_task(react(session, "🔍"))
        
        results = await search_task
        flat_results = results[0] if results else []
        
        if not flat_results:
            if tab_task:
                try: await tab_task
                except: pass
            await session.send("Search returned no results.")
            return

        visible = [r for r in flat_results if not r.get("_hidden", False)]
        
        if not visible:
            if tab_task:
                try: await tab_task
                except: pass
            await session.send("Search returned no visible results.")
            return
        
        # === Filter Mode: Screenshot matching links (NO tab needed) ===
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
                        await session.send(f"⚠️ 序号 {filter_value} 超出范围 (1-{len(visible)})")
                        return
                else:
                    # Link filter: find URLs containing filter term
                    found_count = 0
                    for res in visible:
                        url = res.get("url", "")
                        title = res.get("title", "")
                        # Match filter against both URL and title
                        if (filter_value in url.lower() or filter_value in title.lower()) and url not in urls_to_screenshot:
                            urls_to_screenshot.append(url)
                            found_count += 1
                            if found_count >= count:
                                break
                    
                    if found_count == 0:
                        await session.send(f"⚠️ 未找到包含 \"{filter_value}\" 的链接")
                        return
            
            if not urls_to_screenshot:
                await session.send("⚠️ 未找到匹配的链接")
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
             
        # === Normal Search Mode: Render search results as Sources card ===
        
        # Build references from search results for Sources card
        references = []
        for i, res in enumerate(visible[:10]):
            references.append({
                "title": res.get("title", f"Result {i+1}"),
                "url": res.get("url", ""),
                "snippet": res.get("content", "") or res.get("snippet", ""),
                "original_idx": i + 1,
            })
        
        try:
            tab_id = await tab_task
        except Exception:
            tab_id = None
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name
        
        # Render Sources card with search results (no markdown content, just references)
        render_ok = await core.render(
            markdown_content=f"# 搜索结果: {search_query}",
            output_path=output_path,
            stats={"total_time": 0},
            references=references,
            page_references=[],
            stages_used=[{"name": "search", "description": f"搜索 \"{search_query}\"", "time": 0}],
            tab_id=tab_id
        )
        
        if render_ok and os.path.exists(output_path):
            with open(output_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            
            msg_chain = MessageChain(Image(src=f'data:image/png;base64,{img_data}'))
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
            
            os.remove(output_path)
        else:
            await session.send("渲染搜索结果失败")
        
        search_cache.cleanup()  # Lazy cleanup

    except Exception as e:
        logger.error(f"Search command failed: {e}")
        await session.send(f"Search error: {e}")


metadata("hyw", author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}], version=__version__, config=HywConfig)

# Help command (/h)
alc_help = Alconna(conf.help_command)

@command.on(alc_help)
async def handle_help_command(session: Session[MessageCreatedEvent], result: Arparma):
    """Display help information for all commands."""
    help_text = f"""HYW Plugin v{__version__}

Question Agent:
  • {conf.question_command} tell me...
  • {conf.question_command} [picture] tell me...
Stop Task:
  • {conf.stop_command} (reply to the question/web command)
Web_tool Search:
  • {conf.web_command} query
Web_tool Screenshot:
  • {conf.web_command} https://example.com
Web_tool Filter(search and screenshot):
  • {conf.web_command} github: fastapi
  • {conf.web_command} 1,2: minecraft
  • {conf.web_command} mcmod=2: forge mod
Web_tool Context(screenshot):
  • [quote: web_tool search] + {conf.web_command} 1
  • [quote: web_tool search] + {conf.web_command} 1, 3
"""

    await session.send(help_text)

# Stop command (/x)
alc_stop = Alconna(conf.stop_command)

@command.on(alc_stop)
async def handle_stop_command(session: Session[MessageCreatedEvent], result: Arparma):
    """Stop a running task by replying to the original command message."""
    if not session.reply or not hasattr(session.reply.origin, 'id'):
        await session.send("请回复正在执行的任务消息以停止它")
        return

    target_msg_id = str(session.reply.origin.id)

    if await _task_manager.cancel(target_msg_id):
        # Determine notification based on reaction config
        if conf.reaction:
             asyncio.create_task(react(session, "🛑"))
        else:
             await session.send("正在停止任务...")
    else:
        await session.send("未找到可停止的任务或任务已结束")


@listen(CommandReceive)
async def remove_at(content: MessageChain):
    return content.lstrip(At)
