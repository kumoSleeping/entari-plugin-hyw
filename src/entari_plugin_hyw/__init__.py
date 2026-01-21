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

try:
    __version__ = get_version("entari_plugin_hyw")
except Exception:
    __version__ = "5.0.0-alpha.1"


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
        response = await core.query(request, output_path=output_path)
        
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
            render_ok = response.image_path is not None
        
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
            
            history_manager.remember(
                sent_id, updated_history, [msg_id],
                {"model": model}, context_id, code=display_session_id,
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
    await process_request(session, args.get("all_param"))

metadata("hyw", author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}], version=__version__, config=HywConfig)

@listen(CommandReceive)
async def remove_at(content: MessageChain):
    return content.lstrip(At)
