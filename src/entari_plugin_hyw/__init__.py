from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import time

from arclet.entari import metadata, listen, Session, plugin_config, BasicConfModel, plugin
from arclet.entari import MessageChain, Text, Image, MessageCreatedEvent, Quote, At
from satori.element import Custom
from loguru import logger
import arclet.letoderea as leto
from arclet.entari.event.command import CommandReceive

from .core.hyw import HYW
from .core.history import HistoryManager
from .core.render import ContentRenderer
from .utils.misc import process_onebot_json, process_images
from arclet.entari.event.lifespan import Startup, Ready

import os
import secrets

@dataclass
class HywConfig(BasicConfModel):
    admins: List[str] = field(default_factory=list)
    models: List[Dict[str, Any]] = field(default_factory=list)
    question_command: str = "/q"
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    headless: bool = False
    save_conversation: bool = False
    browser_tool: str = "jina"
    icon: str = "openai"
    jina_api_key: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None
    enable_browser_fallback: bool = False
    reaction: bool = True
    quote: bool = True
    jina_timeout: int = 15

conf = plugin_config(HywConfig)
history_manager = HistoryManager()
renderer = ContentRenderer()
hyw = HYW(config=conf)

class GlobalCache:
    models_image_path: Optional[str] = None

global_cache = GlobalCache()

from satori.exception import ActionFailed

EMOJI_TO_CODE = {
    "✨": "10024",
    "✅": "10004",
    "❌": "10060"
}

async def react(session: Session, emoji: str):
    if not conf.reaction: return
    try:
        if session.event.login.platform == "onebot":
            code = EMOJI_TO_CODE.get(emoji, "10024")
            # OneBot specific reaction
            await session.account.protocol.call_api(
                "internal/set_group_reaction", 
                {
                    "group_id": str(session.guild.id), 
                    "message_id": str(session.event.message.id), 
                    "code": code, 
                    "is_add": True
                }
            )
        else:
            # Standard Satori reaction
            await session.reaction_create(emoji=emoji)
    except ActionFailed:
        pass
    except Exception as e:
        logger.warning(f"Reaction failed: {e}")

async def process_request(session: Session[MessageCreatedEvent], all_param: Optional[MessageChain] = None, 
                         selected_model: Optional[str] = None, selected_vision_model: Optional[str] = None, 
                         conversation_key_override: Optional[str] = None, local_mode: bool = False):
    logger.info(f"Processing request: {all_param}")
    mc = MessageChain(all_param)
    logger.info(f"reply: {session.reply}")
    if session.reply:
        try:
            try:
                if session.reply.origin.user.id == session.event.login.user.id:
                    logger.info("Reply is from me")
                else:
                    mc.extend(MessageChain(" ") + session.reply.origin.message)
            except Exception:
                mc.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception:
            logger.error("Failed to process reply", exc_info=True)
    
    # Filter and reconstruct MessageChain
    filtered_elements = mc.get(Text) + mc.get(Image) + mc.get(Custom)
    mc = MessageChain(filtered_elements)
    logger.info(f"mc: {mc}")

    text_content = str(mc.get(Text)).strip()
    if not text_content and not mc.get(Image) and not mc.get(Custom):
        return

    # History & Context
    hist_key = conversation_key_override
    if not hist_key and session.reply and hasattr(session.reply.origin, 'id'):
        hist_key = history_manager.get_conversation_id(str(session.reply.origin.id))
    
    hist_payload = history_manager.get_history(hist_key) if hist_key else []
    meta = history_manager.get_metadata(hist_key) if hist_key else {}
    context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"

    if conf.reaction: await react(session, "✨")

    try:
        msg_text = mc.get(Text).strip() if mc.get(Text) else ""
        for custom in [e for e in mc if isinstance(e, Custom)]:
            if custom.tag == 'onebot:json':
                if decoded := process_onebot_json(custom.attributes()): msg_text += f"\n{decoded}"
                break
        
        # Model Selection
        model = selected_model or meta.get("model")
        vision_model = selected_vision_model or meta.get("vision_model")

        images, err = await process_images(mc, vision_model)

        # Call Agent
        resp = await hyw.agent(str(mc), conversation_history=hist_payload, images=images, 
                              selected_model=model, selected_vision_model=vision_model, local_mode=local_mode)
        
        # Extract Response Data
        content = resp.get("llm_response", "")
        structured = resp.get("structured_response", {})
        
        # Render
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            output_path = tf.name
        model_used = resp.get("model_used")
        
        icon = conf.icon
        if model_used:
            if m_conf := next((m for m in conf.models if m.get("name") == model_used), None):
                icon = m_conf.get("icon", icon)

        # Calculate turns and session_id
        turns = len([m for m in resp.get("conversation_history", []) if m.get("role") == "user"])
        
        # Determine max_turns
        max_turns = 10
        if model_used:
            if m_conf := next((m for m in conf.models if m.get("name") == model_used), None):
                max_turns = m_conf.get("max_turns", 10)

        # Determine session short code
        if hist_key:
            display_session_id = history_manager.get_code_by_key(hist_key)
            if not display_session_id:
                # Should not happen if key exists, but fallback
                display_session_id = history_manager.generate_short_code()
        else:
            # New conversation, pre-generate code
            display_session_id = history_manager.generate_short_code()

        # Determine vision base url and icon
        vision_base_url = None
        vision_icon = None
        vision_model_used = resp.get("vision_model_used")
        if vision_model_used:
            if v_conf := next((m for m in conf.models if m.get("name") == vision_model_used), None):
                vision_base_url = v_conf.get("base_url")
                vision_icon = v_conf.get("icon")
        
        # Handle Vision Only Mode (suppress text model display)
        render_model_name = model_used or conf.model_name or "unknown"
        render_icon = icon
        render_base_url = m_conf.get("base_url", conf.base_url) if model_used and (m_conf := next((m for m in conf.models if m.get("name") == model_used), None)) else conf.base_url
        
        if not model_used and vision_model_used:
            render_model_name = ""
            render_icon = ""

        await renderer.render(
            markdown_content=content,
            output_path=output_path,
            suggestions=structured.get("speculation", []),
            stats=resp.get("stats", {}),
            references=structured.get("references", []),
            model_name=render_model_name,
            search_provider="Jina Fetch" if conf.browser_tool == "jina" else "Playwright",
            icon_config=render_icon,
            vision_model_name=vision_model_used,
            vision_base_url=vision_base_url,
            vision_icon_config=vision_icon,
            base_url=render_base_url,
            session_id=display_session_id,
            turns=turns,
            max_turns=max_turns
        )
        
        # Send & Save
        from pathlib import Path
        
        # Handle 'send' field from structured response
        if structured.get("send"):
            try:
                if conf.quote:
                    await session.send([Quote(session.event.message.id), structured["send"]])
                else:
                    await session.send(structured["send"])
            except Exception as e:
                logger.warning(f"Failed to send extra content: {e}")

        msg_chain = MessageChain(Image.of(path=Path(output_path).absolute()))
        if conf.quote:
            msg_chain.insert(0, Quote(session.event.message.id))
            
        sent = await session.send(msg_chain)
        
        sent_id = next((str(e.id) for e in sent if hasattr(e, 'id')), None) if sent else None
        msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)
        related = [msg_id] + ([str(session.reply.origin.id)] if session.reply and hasattr(session.reply.origin, 'id') else [])
        
        history_manager.remember(sent_id, resp.get("conversation_history", []), related, {"model": model_used}, context_id, code=display_session_id)
        
        if conf.save_conversation and sent_id:
            history_manager.save_to_disk(sent_id)


    except Exception as e:
        logger.exception(f"Error: {e}")
        await session.send(f"Error: {e}")

# Commands
from .command import question

metadata("hyw", author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}], version="2.3.3", config=HywConfig)

@leto.on(CommandReceive)
async def remove_at(content: MessageChain):
    content = content.lstrip(At)
    return content


@leto.on(Startup)
async def on_startup():
    try:
        output_dir = "data/cache"
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/models_list_cache.png"
        
        logger.info("Generating models list cache...")
        await renderer.render_models_list(conf.models, output_path, default_base_url=HywConfig.base_url)
        global_cache.models_image_path = os.path.abspath(output_path)
        logger.info(f"Models list cached at: {global_cache.models_image_path}")
    except Exception as e:
        logger.error(f"Failed to cache models list: {e}")


@leto.on(MessageCreatedEvent)
async def on_message_created(session: Session[MessageCreatedEvent]):
    text = str(MessageChain(session.event.message).get(Text)).strip()
    prefixes = [conf.question_command]
    if any(text.startswith(p) for p in prefixes): return
    
    if session.reply:
        qid = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
        if qid and history_manager.get_conversation_id(qid):
            await process_request(session, MessageChain(session.event.message))


