from dataclasses import dataclass, field
from importlib.metadata import version as get_version
from typing import List, Dict, Any, Optional, Union
import time
import asyncio

# 从 pyproject.toml 读取版本号，避免重复维护
try:
    __version__ = get_version("entari_plugin_hyw")
except Exception:
    __version__ = "0.0.0"

from arclet.alconna import Alconna, Args, AllParam, CommandMeta, Option, Arparma, MultiVar, store_true
from arclet.entari import metadata, listen, Session, plugin_config, BasicConfModel, plugin, command
from arclet.letoderea import on
from arclet.entari import MessageChain, Text, Image, MessageCreatedEvent, Quote, At
from satori.element import Custom
from loguru import logger
import arclet.letoderea as leto
from arclet.entari.event.command import CommandReceive

from .modular_pipeline import ModularPipeline
from .history import HistoryManager
from .render_vue import ContentRenderer, get_content_renderer
from .misc import process_onebot_json, process_images, resolve_model_name, render_refuse_answer, render_image_unsupported, REFUSE_ANSWER_MARKDOWN
from arclet.entari.event.lifespan import Cleanup

import os
import secrets
import base64

import re


def parse_color(color: str) -> str:
    """
    Parse color from hex or RGB tuple to hex format.
    Supports: #ff0000, ff0000, (255, 0, 0), 255,0,0
    """
    if not color:
        return "#ef4444"
    
    color = str(color).strip()
    
    # Hex format: #fff or #ffffff or ffffff
    if color.startswith('#') and len(color) in [4, 7]:
        return color
    if re.match(r'^[0-9a-fA-F]{6}$', color):
        return f'#{color}'
    
    # RGB tuple: (r, g, b) or r,g,b
    rgb_match = re.match(r'^\(?(\d+)[,\s]+(\d+)[,\s]+(\d+)\)?$', color)
    if rgb_match:
        r, g, b = (max(0, min(255, int(x))) for x in rgb_match.groups())
        return f'#{r:02x}{g:02x}{b:02x}'
    
    logger.warning(f"Invalid color '{color}', using default #ef4444")
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
        if len(self._seen) > self.max_size:
            for k, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[: len(self._seen) - self.max_size]:
                self._seen.pop(k, None)

_event_deduper = _RecentEventDeduper()

@dataclass
class ModelConfig:
    """Model configuration for a specific stage."""
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None
    model_provider: Optional[str] = None
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    image_input: bool = True


@dataclass
class HywConfig(BasicConfModel):
    # Core Settings
    admins: List[str] = field(default_factory=list)
    models: List[Dict[str, Any]] = field(default_factory=list)
    question_command: str = "/q"
    language: str = "Simplified Chinese"
    temperature: float = 0.4
    
    # Root-level defaults (backward compatible)
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    extra_body: Optional[Dict[str, Any]] = None
    model_provider: Optional[str] = None
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    
    # Nested Stage Configs
    instruct: Optional[ModelConfig] = None
    qa: Optional[ModelConfig] = None
    main: Optional[ModelConfig] = None  # Summary stage
    
    # Search/Fetch Settings
    search_engine: str = "google"
    
    # Rendering Settings
    headless: bool = False
    render_timeout_ms: int = 6000
    render_image_timeout_ms: int = 3000
    
    # Bot Behavior
    save_conversation: bool = False
    reaction: bool = False
    quote: bool = False
    
    # UI Theme
    theme_color: str = "#ff0000"
    
    def __post_init__(self):
        """Parse and normalize theme color after initialization."""
        self.theme_color = parse_color(self.theme_color)
        # Convert dicts to ModelConfig if needed
        if isinstance(self.instruct, dict):
            self.instruct = ModelConfig(**self.instruct)
        if isinstance(self.qa, dict):
            self.qa = ModelConfig(**self.qa)
        if isinstance(self.main, dict):
            self.main = ModelConfig(**self.main)
    
    def get_model_config(self, stage: str) -> Dict[str, Any]:
        """
        Get resolved model config for a stage.
        
        Args:
            stage: "instruct", "qa", or "main" (summary)
        
        Returns:
            Dict with model_name, api_key, base_url, extra_body, etc.
        """
        # Determine primary and secondary config sources
        primary = None
        secondary = None
        
        if stage == "instruct":
            primary = self.instruct
            secondary = self.main # Fallback to main
        elif stage == "qa":
            # QA fallback to main as well if ever used
            primary = self.qa
            secondary = self.main
        elif stage == "main":
            primary = self.main
            
        # Build result with fallback logic
        def resolve(field_name: str, is_essential: bool = True):
            """Resolve a field with fallback: Primary -> Secondary -> Root."""
            # 1. Try Primary
            val = getattr(primary, field_name, None) if primary else None
            
            # 2. Try Secondary (if value missing)
            if val is None and secondary:
                val = getattr(secondary, field_name, None)
                
            # 3. Try Root (if value still missing)
            if val is None:
                val = getattr(self, field_name, None)
            return val
        
        return {
            "model_name": resolve("model_name"),
            "api_key": resolve("api_key"),
            "base_url": resolve("base_url"),
            "extra_body": resolve("extra_body", is_essential=False),
            "model_provider": resolve("model_provider", is_essential=False),
            "input_price": resolve("input_price", is_essential=False),
            "output_price": resolve("output_price", is_essential=False),
        }


conf = plugin_config(HywConfig)
history_manager = HistoryManager()
renderer = ContentRenderer(headless=conf.headless)
from .render_vue import set_global_renderer
set_global_renderer(renderer)

# Pre-start Crawl4AI browser for fast fetching/screenshots
from .browser.service import prestart_browser, close_screenshot_service
# prestart_browser(headless=conf.headless)  # Removed to avoid RuntimeError: no running event loop


class GlobalCache:
    models_image_path: Optional[str] = None

global_cache = GlobalCache()


@listen(Cleanup)
async def cleanup_screenshot_service():
    """Cleanup shared browser on shutdown."""
    try:
        await close_screenshot_service()
        # Also close the shared browser manager
        from .browser.manager import close_shared_browser
        await close_shared_browser()
    except Exception as e:
        logger.warning(f"Failed to cleanup browser services: {e}")

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
    selected_vision_model: Optional[str] = None,
    conversation_key_override: Optional[str] = None,
    local_mode: bool = False,
) -> None:
    mc = MessageChain(all_param)
    if session.reply:
        try:
            # Check if reply is from self (the bot)
            # 1. Check by Message ID (reliable for bot's own messages if recorded)
            reply_msg_id = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
            is_bot = False
            
            if reply_msg_id and history_manager.is_bot_message(reply_msg_id):
                is_bot = True

            if is_bot:
                pass  # Reply is from bot - ignoring
            else:
                mc.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception as e:
            logger.warning(f"Failed to process reply origin: {e}")
            mc.extend(MessageChain(" ") + session.reply.origin.message)
    
    # Filter and reconstruct MessageChain
    filtered_elements = mc.get(Text) + mc.get(Image) + mc.get(Custom)
    mc = MessageChain(filtered_elements)


    text_content = str(mc.get(Text)).strip()
    # Remove HTML image tags from text content to prevent "unreasonable code behavior"
    text_content = re.sub(r'<img[^>]+>', '', text_content, flags=re.IGNORECASE)

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
        msg_text = str(mc.get(Text)).strip() if mc.get(Text) else ""
        msg_text = re.sub(r'<img[^>]+>', '', msg_text, flags=re.IGNORECASE)
        
        # If message is empty but has images, use a placeholder
        if not msg_text and (mc.get(Image) or mc.get(Custom)):
             msg_text = "[图片]"
        
        for custom in [e for e in mc if isinstance(e, Custom)]:
            if custom.tag == 'onebot:json':
                if decoded := process_onebot_json(custom.attributes()): msg_text += f"\n{decoded}"
                break
        
        # Model Selection (Step 1)
        # Resolve model names from config if they are short names/keywords
        model = selected_model or meta.get("model")
        if model and model != "off":
            resolved, err = resolve_model_name(model, conf.models)
            if resolved:
                model = resolved
            elif err:
                logger.warning(f"Model resolution warning for {model}: {err}")

        vision_model = selected_vision_model or meta.get("vision_model")
        if vision_model and vision_model != "off":
            resolved_v, err_v = resolve_model_name(vision_model, conf.models)
            if resolved_v:
                vision_model = resolved_v
            elif err_v:
                logger.warning(f"Vision model resolution warning for {vision_model}: {err_v}")

        images, err = await process_images(mc, vision_model)
        
        # Check image input support
        model_cfg_dict = next((m for m in conf.models if m.get("name") == model), None)
        image_input_supported = True
        if model_cfg_dict:
            image_input_supported = model_cfg_dict.get("image_input", True)
        
        # Log inferenced content mode
        inferred_content_mode = "image" if image_input_supported else "text"
        logger.info(f"Process Request: Model '{model}' Image Input: {image_input_supported} -> Mode: {inferred_content_mode}")
        
        if images and not image_input_supported:
            logger.warning(f"Model '{model}' does not support images, but user sent {len(images)} images.")
            
            # Start renderer for the unsupported card
            renderer = await get_content_renderer()
            render_tab_task = asyncio.create_task(renderer.prepare_tab())
            
            # Wait for tab and render unsupported
            try:
                tab_id = await render_tab_task
            except Exception as e:
                tab_id = None
                
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                output_path = tf.name
                
            render_ok = await render_image_unsupported(
                renderer=renderer,
                output_path=output_path,
                theme_color=conf.theme_color,
                tab_id=tab_id
            )
            
            if render_ok:
                with open(output_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode()
                await session.send(MessageChain(Image(src=f'data:image/png;base64,{img_data}')))
                if os.path.exists(output_path):
                    os.remove(output_path)
            return

        renderer = await get_content_renderer()
        render_tab_task = asyncio.create_task(renderer.prepare_tab())
        tab_id = None

        # Call Pipeline directly
        safe_input = msg_text
        pipeline = ModularPipeline(conf)
        try:
            resp = await pipeline.execute(
                safe_input,
                hist_payload,
                model_name=model,
                images=images,
                selected_vision_model=vision_model,
            )
        finally:
            await pipeline.close()
        
        # Step 1 Results
        step1_vision_model = resp.get("vision_model_used")
        step1_model = resp.get("model_used")
        step1_history = resp.get("conversation_history", [])
        step1_stats = resp.get("stats", {})
        
        final_resp = resp
        
        # Step 2 (Optional)

            
        
        # Extract Response Data
        content = final_resp.get("llm_response", "")
        structured = final_resp.get("structured_response", {})
        
        # Wait for tab preparation if needed (should be ready by now)
        try:
            tab_id = await render_tab_task
        except Exception as e:
            logger.warning(f"Failed to prepare render tab: {e}")
            tab_id = None
        
        # Render
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name
        model_used = final_resp.get("model_used")

        # Determine session short code
        if hist_key:
            display_session_id = history_manager.get_code_by_key(hist_key)
            if not display_session_id:
                display_session_id = history_manager.generate_short_code()
        else:
            display_session_id = history_manager.generate_short_code()

        # Use stats_list if available, otherwise standard stats
        stats_to_render = final_resp.get("stats_list", final_resp.get("stats", {}))
        
        # Check if refuse_answer was triggered
        if final_resp.get("refuse_answer"):
            logger.info(f"Refuse answer triggered. Rendering refuse image. Reason: {final_resp.get('refuse_reason', '')}")
            render_ok = await render_refuse_answer(
                renderer=renderer,
                output_path=output_path,
                reason=final_resp.get('refuse_reason', 'Instruct 专家分配此任务流程失败，请尝试提出其他问题~'),
                theme_color=conf.theme_color,
                tab_id=tab_id,
            )
        else:
            render_ok = await renderer.render(
                markdown_content=content,
                output_path=output_path,
                tab_id=tab_id,
                stats=stats_to_render,
                references=structured.get("references", []),
                page_references=structured.get("page_references", []),
                image_references=structured.get("image_references", []),
                stages_used=final_resp.get("stages_used", []),
                theme_color=conf.theme_color,
            )
        
        # Send & Save
        if not render_ok:
            logger.error("Render failed; skipping reply.")
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception as exc:
                    logger.warning(f"Failed to delete render output {output_path}: {exc}")
            sent = None
        else:
            # Convert to base64
            with open(output_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
                
            # Build single reply chain (image only now)
            elements = []
            elements.append(Image(src=f'data:image/png;base64,{img_data}'))

            msg_chain = MessageChain(*elements)
            
            if conf.quote:
                msg_chain = MessageChain(Quote(session.event.message.id)) + msg_chain
                
            # Use reply_to instead of manual Quote insertion to avoid ActionFailed errors
            sent = await session.send(msg_chain)
        
        sent_id = next((str(e.id) for e in sent if hasattr(e, 'id')), None) if sent else None
        msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)
        related = [msg_id] + ([str(session.reply.origin.id)] if session.reply and hasattr(session.reply.origin, 'id') else [])
        
        history_manager.remember(
            sent_id,
            final_resp.get("conversation_history", []),
            related,
            {
                "model": model_used,
                "trace_markdown": final_resp.get("trace_markdown"),
            },
            context_id,
            code=display_session_id,
        )
        
        if conf.save_conversation and sent_id:
            try:
                # Pass web_results to save fetched pages as markdown, and output image
                history_manager.save_to_disk(
                    sent_id, 
                    web_results=final_resp.get("web_results"),
                    image_path=output_path if 'output_path' in locals() else None
                )
            except Exception as e:
                logger.warning(f"Failed to save conversation: {e}")
        
        # Cleanup temp image
        if 'output_path' in locals() and output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        



    except Exception as e:
        logger.exception(f"Error: {e}")
        err_msg = f"Error: {e}"
        if conf.quote:
             await session.send([Quote(session.event.message.id), err_msg])
        else:
             await session.send(err_msg)
        
        # Save conversation on error if response was generated
        if 'resp' in locals() and resp and conf.save_conversation:
            try:
                # Use a temporary ID for error cases
                error_id = f"error_{int(time.time())}_{secrets.token_hex(4)}"
                
                # Try to salvage history
                partial_hist = []
                if 'resp' in locals() and resp:
                    partial_hist = resp.get("conversation_history", [])
                elif 'context' in locals() and context and hasattr(context, 'instruct_history'):
                    partial_hist = context.instruct_history
                    
                related_ids = []
                if 'session' in locals():
                     msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)
                     related_ids = [msg_id]

                history_manager.remember(error_id, partial_hist, related_ids, {"model": "error", "error": str(e)}, context_id, code=display_session_id if 'display_session_id' in locals() else None)
                
                # Save debug data on error
                web_res = context.web_results if 'context' in locals() and context else []
                
                history_manager.save_to_disk(
                    error_id,
                    web_results=web_res
                )

            except Exception as save_err:
                logger.error(f"Failed to save error conversation: {save_err}")


alc = Alconna(
    conf.question_command,
    Args["all_param;?", AllParam],
)

@command.on(alc)    
async def handle_question_command(session: Session[MessageCreatedEvent], result: Arparma):
    """Handle main Question command"""
    try:
        logger.info(f"Question Command Triggered. Message: {result}")
        mid = str(session.event.message.id) if getattr(session.event, "message", None) else str(session.event.id)
        dedupe_key = f"{getattr(session.account, 'id', 'account')}:{mid}"
        if _event_deduper.seen_recently(dedupe_key):
            logger.warning(f"Duplicate command event ignored: {dedupe_key}")
            return
    except Exception:
        pass

    logger.info(f"Question Command Triggered. Message: {session.event.message}")
    
    args = result.all_matched_args
    
    await process_request(session, args.get("all_param"), selected_model=None, selected_vision_model=None, conversation_key_override=None)

metadata("hyw", author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}], version=__version__, config=HywConfig)


@listen(CommandReceive)
async def remove_at(content: MessageChain):
    return content.lstrip(At)
