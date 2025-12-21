from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union
import time

from arclet.alconna import Alconna, Args, AllParam, CommandMeta, Option, Arparma, MultiVar, store_true
from arclet.entari import metadata, listen, Session, plugin_config, BasicConfModel, plugin, command
from arclet.entari import MessageChain, Text, Image, MessageCreatedEvent, Quote, At
from satori.element import Custom
from loguru import logger
import arclet.letoderea as leto
from arclet.entari.event.command import CommandReceive

from .core.hyw import HYW
from .core.history import HistoryManager
from .core.render import ContentRenderer
from .utils.misc import process_onebot_json, process_images, resolve_model_name
from arclet.entari.event.lifespan import Startup, Ready, Cleanup

import os
import secrets
import base64

import re

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
class HywConfig(BasicConfModel):
    admins: List[str] = field(default_factory=list)
    models: List[Dict[str, Any]] = field(default_factory=list)
    question_command: str = "/q"
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    vision_model_name: Optional[str] = None
    vision_api_key: Optional[str] = None
    vision_base_url: Optional[str] = None
    vision_system_prompt: Optional[str] = None
    intruct_model_name: Optional[str] = None
    intruct_api_key: Optional[str] = None
    intruct_base_url: Optional[str] = None
    intruct_system_prompt: Optional[str] = None
    agent_system_prompt: Optional[str] = None
    playwright_mcp_command: str = "npx"
    playwright_mcp_args: Optional[List[str]] = None
    search_base_url: str = "https://duckduckgo.com/?q={query}&format=json&results_per_page={limit}"
    image_search_base_url: str = "https://duckduckgo.com/?q={query}&iax=images&ia=images&format=json&results_per_page={limit}"
    headless: bool = False
    save_conversation: bool = False
    icon: str = "openai"
    render_timeout_ms: int = 6000
    extra_body: Optional[Dict[str, Any]] = None
    enable_browser_fallback: bool = False
    reaction: bool = True
    quote: bool = True
    temperature: float = 0.4
    # Billing configuration (price per million tokens)
    input_price: Optional[float] = None  # $ per 1M input tokens
    output_price: Optional[float] = None  # $ per 1M output tokens
    # Vision model pricing overrides (defaults to main model pricing if not set)
    vision_input_price: Optional[float] = None
    vision_output_price: Optional[float] = None
    # Instruct model pricing overrides (defaults to main model pricing if not set)
    intruct_input_price: Optional[float] = None
    intruct_output_price: Optional[float] = None
    
    # Provider Names
    search_name: str = "SearXNG"
    search_provider: str = "SearXNG"
    model_provider: Optional[str] = None
    vision_model_provider: Optional[str] = None
    intruct_model_provider: Optional[str] = None
    
    start_test: Optional[Union[str, bool]] = None

conf = plugin_config(HywConfig)
history_manager = HistoryManager()
renderer = ContentRenderer()
hyw = HYW(config=conf)

@listen(Ready, once=True)
async def _hyw_warmup_mcp():
    try:
        await hyw.pipeline.warmup_mcp()
    except Exception as e:
        logger.warning(f"MCP Playwright warmup error: {e}")

@listen(Ready, once=True)
async def _run_ui_test():
    """Run UI rendering test on startup if configured."""
    # Debug log to confirm listener is active
    logger.info(f"UI TEST Listener Active. start_test config: {conf.start_test} (type: {type(conf.start_test)})")
    
    if not conf.start_test:
        return
        
    test_file = ""
    if isinstance(conf.start_test, str):
        test_file = conf.start_test
    elif conf.start_test is True:
        # User enabled boolean toggle, assume default path
        # Try a few locations
        candidates = ["data/conversations/ui-test.md", "ui-test.md", "README.md"]
        for c in candidates:
            if os.path.exists(c):
                test_file = c
                break
        if not test_file:
            logger.warning("UI TEST: start_test=True but no default test file found (tried: data/conversations/ui-test.md, ui-test.md, README.md)")
            return
            
    logger.info(f"UI TEST: Starting render test with file {test_file}")
    
    if not os.path.exists(test_file):
        logger.error(f"UI TEST: File not found: {test_file}")
        return
        
    try:
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Mock Data for Full UI Test
        stats = {
            "time": 12.5,
            "vision_duration": 3.2,
            "cost": 0.0015
        }
        
        stages = [
            {"name": "Vision", "model": "google/gemini-pro-vision", "time": 3.2, "cost": 0.0005, "provider": "Google"},
            {"name": "Search", "model": "duckduckgo", "time": 1.5, "cost": 0.0, "provider": "DDG"},
            {"name": "Agent", "model": "anthropic/claude-3-5-sonnet", "time": 7.8, "cost": 0.0010, "provider": "Anthropic"}
        ]
        
        mcp_steps = [
            {"name": "search_google", "description": "searching for 'latest entari news'", "icon": "search"},
            {"name": "visit_page", "description": "visiting python.org", "icon": "navigate"},
            {"name": "click_element", "description": "clicking 'Downloads'", "icon": "click"}
        ]
        
        references = [
            {"title": "Entari Docs", "url": "https://entari.onebot.dev", "domain": "entari.onebot.dev"},
            {"title": "Python Language", "url": "https://python.org", "domain": "python.org"}
        ]
        
        output_dir = "data/cache"
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/ui_test_result.png"
        
        logger.info(f"UI TEST: Rendering to {output_path}...")
        
        start = time.time()
        success = await renderer.render(
            markdown_content=content,
            output_path=output_path,
            stats=stats,
            stages_used=stages,
            mcp_steps=mcp_steps,
            references=references,
            model_name="CLAUDE-3-5-SONNET",
            provider_name="Anthropic",
            behavior_summary="Automated Test",
            icon_config="anthropic",
            render_timeout_ms=10000
        )
        
        if success:
            logger.success(f"UI TEST: Render completed in {time.time() - start:.2f}s. Saved to {output_path}")
        else:
            logger.error("UI TEST: Render FAILED.")
            
    except Exception as e:
        logger.error(f"UI TEST: Exception during test: {e}")


@listen(Cleanup, once=True)
async def _hyw_cleanup():
    try:
        await hyw.close()
    except Exception as e:
        logger.warning(f"HYW cleanup error: {e}")

class GlobalCache:
    models_image_path: Optional[str] = None

global_cache = GlobalCache()

from satori.exception import ActionFailed
from satori.adapters.onebot11.reverse import _Connection

# Monkeypatch to suppress ActionFailed for get_msg
original_call_api = _Connection.call_api

async def patched_call_api(self, action: str, params: dict = None):
    try:
        return await original_call_api(self, action, params)
    except ActionFailed as e:
        if action == "get_msg":
            logger.warning(f"Suppressed ActionFailed for get_msg: {e}")
            return None
        raise e

_Connection.call_api = patched_call_api

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
                         conversation_key_override: Optional[str] = None, local_mode: bool = False,
                         next_prompt: Optional[str] = None, next_text_model: Optional[str] = None, next_vision_model: Optional[str] = None):
    logger.info(f"Processing request: {all_param}")
    mc = MessageChain(all_param)
    logger.info(f"reply: {session.reply}")
    if session.reply:
        try:
            # Check if reply is from self (the bot)
            # 1. Check by Message ID (reliable for bot's own messages if recorded)
            reply_msg_id = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
            is_bot = False
            
            if reply_msg_id and history_manager.is_bot_message(reply_msg_id):
                is_bot = True
                logger.info(f"Reply target {reply_msg_id} identified as bot message via history")

            if is_bot:
                logger.info("Reply is from me - ignoring content")
            else:
                logger.info(f"Reply is from user (or unknown) - including content")
                mc.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception as e:
            logger.warning(f"Failed to process reply origin: {e}")
            mc.extend(MessageChain(" ") + session.reply.origin.message)
    
    # Filter and reconstruct MessageChain
    filtered_elements = mc.get(Text) + mc.get(Image) + mc.get(Custom)
    mc = MessageChain(filtered_elements)
    logger.info(f"mc: {mc}")

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

        # Call Agent (Step 1)
        # Sanitize user_input: use extracted text only
        safe_input = msg_text
            
        resp = await hyw.agent(safe_input, conversation_history=hist_payload, images=images, 
                              selected_model=model, selected_vision_model=vision_model, local_mode=local_mode)
        
        # Step 1 Results
        step1_vision_model = resp.get("vision_model_used")
        step1_model = resp.get("model_used")
        step1_history = resp.get("conversation_history", [])
        step1_stats = resp.get("stats", {})
        
        final_resp = resp
        
        # Step 2 (Optional)
        if next_prompt:
            logger.info(f"Executing Step 2 with prompt: {next_prompt}")
            
            # Use Step 1 history as base for Step 2
            # hyw.agent already returns the updated history including the new turn
            # So we just pass step1_history
            
            # Determine Step 2 models
            # If not specified, inherit from Step 1 or config? 
            # Usually inherit from config or meta if not specified in -n
            step2_model = next_text_model or model
            if step2_model and step2_model != "off":
                resolved_s2, err_s2 = resolve_model_name(step2_model, conf.models)
                if resolved_s2:
                    step2_model = resolved_s2
            
            step2_vision_model = next_vision_model or vision_model # Probably not used if no new images, but consistent
            if step2_vision_model and step2_vision_model != "off":
                resolved_s2v, err_s2v = resolve_model_name(step2_vision_model, conf.models)
                if resolved_s2v:
                    step2_vision_model = resolved_s2v
            
            # No new images for Step 2 usually, unless we want to carry over images?
            # The user said "First round image model, second round text model".
            # Usually Step 2 is text-only follow-up.
            # But hyw.agent stateless? No, we pass history.
            # We don't pass 'images' again to Step 2 unless we want them re-analyzed.
            # If Step 1 analyzed images, the analysis is in history (as assistant message or system message?).
            # In hyw.agent, image analysis result is added to history.
            # So we don't need to pass images again.
            
            resp2 = await hyw.agent(str(next_prompt), conversation_history=step1_history, images=None,
                                   selected_model=step2_model, selected_vision_model=step2_vision_model, local_mode=local_mode)
            
            final_resp = resp2
            
            # Merge Stats
            # Instead of merging into a single dict, we prepare a list of stats for the renderer
            # But we also need a combined stats for history recording?
            # History manager likely expects a single dict or doesn't care much (it stores what we give)
            
            # Let's keep step1_stats and resp2["stats"] separate for rendering
            # But for history, maybe we still want a merged one?
            # The code below uses final_resp["stats"] for rendering AND history.
            
            # Let's create a list for rendering
            stats_for_render = [step1_stats, resp2.get("stats", {})]
            
            # And a merged one for history/final_resp
            merged_stats = step1_stats.copy()
            if "stats" in resp2:
                for k, v in resp2["stats"].items():
                    if isinstance(v, (int, float)) and k in merged_stats:
                        merged_stats[k] += v
                    elif k == "visited_domains":
                        merged_stats[k] = list(set(merged_stats.get(k, []) + v))
                    else:
                        merged_stats[k] = v
            
            final_resp["stats"] = merged_stats
            final_resp["stats_list"] = stats_for_render # Pass this to renderer if available
            
            # Merge Model Info for Display
            # We want to show Step 1 Vision Model AND Step 2 Text Model
            if step1_vision_model:
                final_resp["vision_model_used"] = step1_vision_model
            # final_resp["model_used"] is already from Step 2
            
        
        # Extract Response Data
        content = final_resp.get("llm_response", "")
        structured = final_resp.get("structured_response", {})
        
        # Render
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            output_path = tf.name
        model_used = final_resp.get("model_used")
        vision_model_used = final_resp.get("vision_model_used")
        
        # Helper to infer icon from model name
        def infer_icon_from_model(model_name: str) -> str:
            """Infer icon name from model name (e.g. 'google/gemini-3-flash' -> 'google' or 'gemini')"""
            if not model_name:
                return conf.icon
            name_lower = model_name.lower()
            # Check for known providers/models in the name
            known_icons = ["google", "gemini", "openai", "anthropic", "deepseek", "mistral", 
                          "qwen", "grok", "xai", "perplexity", "microsoft", "minimax", "nvidia"]
            for icon_name in known_icons:
                if icon_name in name_lower:
                    return icon_name
            return conf.icon
        
        icon = conf.icon
        m_conf = None
        if model_used:
            m_conf = next((m for m in conf.models if m.get("name") == model_used), None)
            if m_conf:
                icon = m_conf.get("icon", infer_icon_from_model(model_used))
            else:
                # Model not in config list, infer from name
                icon = infer_icon_from_model(model_used)

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
        
        if vision_model_used:
            v_conf = next((m for m in conf.models if m.get("name") == vision_model_used), None)
            if v_conf:
                vision_base_url = v_conf.get("base_url")
                vision_icon = v_conf.get("icon", infer_icon_from_model(vision_model_used))
            else:
                vision_icon = infer_icon_from_model(vision_model_used)
        
        # Handle Vision Only Mode (suppress text model display)
        render_model_name = model_used or conf.model_name or "unknown"
        render_icon = icon
        render_base_url = m_conf.get("base_url", conf.base_url) if m_conf else conf.base_url
        
        if not model_used and vision_model_used:
            render_model_name = ""
            render_icon = ""

        # Use stats_list if available, otherwise standard stats
        stats_to_render = final_resp.get("stats_list", final_resp.get("stats", {}))

        # Determine Behavior Summary & Provider Name
        
        # 1. Behavior Summary
        behavior_summary = "Text Generation"
        if structured.get("mcp_steps"):
            behavior_summary = "Agentic Loop"
        elif vision_model_used:
             behavior_summary = "Visual Analysis"
        
        # 2. Provider Name
        # Try to get from m_conf (resolved above)
        provider_name = "Unknown Provider"
        if model_used and m_conf:
            provider_name = m_conf.get("provider", "Unknown Provider")
        elif not model_used and vision_model_used:
             # If only vision model used (unlikely but possible in code logic)
             if 'v_conf' in locals() and v_conf:
                 provider_name = v_conf.get("provider", "Unknown Provider")
        
        # If still unknown and we have base_url, maybe use domain as last resort fallback?
        # User said: "provider does not automatically get from url if not filled"
        # So if it's "Unknown Provider", we leave it or maybe empty string?
        # Let's stick to "Unknown Provider" or just empty if we want to be clean.
        # But for UI validation it's better to show something if missing config.
             
        render_ok = await renderer.render(
            markdown_content=content,
            output_path=output_path,
            suggestions=[],
            stats=stats_to_render,
            references=structured.get("references", []),
            mcp_steps=structured.get("mcp_steps", []),
            stages_used=final_resp.get("stages_used", []),
            model_name=render_model_name,
            provider_name=provider_name,
            behavior_summary=behavior_summary,
            icon_config=render_icon,
            vision_model_name=vision_model_used,
            vision_base_url=vision_base_url,
            vision_icon_config=vision_icon,
            base_url=render_base_url,
            billing_info=final_resp.get("billing_info"),
            render_timeout_ms=conf.render_timeout_ms
        )
        
        # Send & Save
        if not render_ok:
            logger.error("Render failed; skipping reply. Check browser/playwright status.")
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
            history_manager.save_to_disk(sent_id)


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
                history_manager.remember(error_id, resp.get("conversation_history", []), [], {"model": model_used if 'model_used' in locals() else "unknown", "error": str(e)}, context_id, code=display_session_id if 'display_session_id' in locals() else None)
                history_manager.save_to_disk(error_id)
                logger.info(f"Saved error conversation to {error_id}")
            except Exception as save_err:
                logger.error(f"Failed to save error conversation: {save_err}")

# Secondary Parser for -n content
next_alc = Alconna(
    "next",
    Option("-v|--vision", Args["vision_model", str], help_text="设置视觉模型(设为off禁用)"),
    Option("-t|--text", Args["text_model", str], help_text="设置文本模型"),
    Args["prompt", AllParam],
)

# Main Command (Question)
alc = Alconna(
    conf.question_command,
    Option("-v|--vision", Args["vision_model", str]),
    Option("-t|--text", Args["text_model", str]),
    Option("-c|--code", Args["code", str]),
    Option("-n|--next", Args["next_input", AllParam]),
    Args["list_models;?", "-m|--models"],
    Args["all_chat;?", "-a"],
    Args["local_mode;?", "-l"],
    Args["all_param?", MultiVar(str | Image | Custom)],
    meta=CommandMeta(
        compact=False, 
        description=f"""使用方法:
        {conf.question_command} -a : 列出所有会话
        {conf.question_command} -m : 列出所有模型
        {conf.question_command} -v <模型名> : 设置主要视觉模型, 设为 off 禁用
        {conf.question_command} -t <模型名> : 设置主要文本模型
        {conf.question_command} -l : 开启本地模式 (关闭Web索引)
        {conf.question_command} -c <4位消息码> : 继续指定会话
        {conf.question_command} -n <后续提示词> : 在第一步完成后执行后续操作 (支持 -t/-v)
        {conf.question_command} <问题> : 发起问题
特性:
"""
        )
)

@command.on(alc)
async def handle_question_command(session: Session[MessageCreatedEvent], result: Arparma):
    """Handle main Question command"""
    try:
        mid = str(session.event.message.id) if getattr(session.event, "message", None) else str(session.event.id)
        dedupe_key = f"{getattr(session.account, 'id', 'account')}:{mid}"
        if _event_deduper.seen_recently(dedupe_key):
            logger.warning(f"Duplicate command event ignored: {dedupe_key}")
            return
    except Exception:
        pass

    logger.info(f"Question Command Triggered. Message: {session.event.message}")
    
    args = result.all_matched_args
    logger.info(f"Matched Args: {args}")
    
    text_model_val = args.get("text_model")
    vision_model_val = args.get("vision_model")
    code_val = args.get("code")
    all_flag_val = args.get("all_chat")
    list_models_val = args.get("list_models")
    local_mode_val = True if args.get("local_mode") else False
    logger.info(f"Local mode: {local_mode_val} (type: {type(local_mode_val)})")
    
    # Handle -m (List Models)
    if list_models_val:
        # global_cache is already imported/defined in __init__.py
        
        if global_cache.models_image_path and os.path.exists(global_cache.models_image_path):
            logger.info(f"Using cached models list: {global_cache.models_image_path}")
            with open(global_cache.models_image_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            msg = MessageChain(Image(src=f'data:image/png;base64,{img_data}'))
            if conf.quote: msg = MessageChain(Quote(session.event.message.id)) + msg
            await session.send(msg)
            return

        output_dir = "data/cache"
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/models_list_cache.png"
        
        await renderer.render_models_list(
            conf.models,
            output_path,
            default_base_url=conf.base_url,
            render_timeout_ms=conf.render_timeout_ms,
        )
        global_cache.models_image_path = os.path.abspath(output_path)
        
        with open(output_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        msg = MessageChain(Image(src=f'data:image/png;base64,{img_data}'))
        if conf.quote: msg = MessageChain(Quote(session.event.message.id)) + msg
        await session.send(msg)
        return
    
    # Handle -a (List History)
    if all_flag_val:
        context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"
        keys = history_manager.list_by_context(context_id, limit=10)
        if not keys:
            msg = "暂无历史会话"
            if conf.quote: await session.send([Quote(session.event.message.id), msg])
            else: await session.send(msg)
            return
            
        msg = "历史会话 [最近10条]\n"
        for i, key in enumerate(keys):
            short_code = history_manager.get_code_by_key(key) or "????"
            hist = history_manager.get_history(key)
            preview = "..."
            if hist and len(hist) > 0:
                last_content = hist[-1].get("content", "")
                preview = (last_content[:20] + "...") if len(last_content) > 20 else last_content
            
            msg += f"{short_code} {preview}\n"
        if conf.quote: await session.send([Quote(session.event.message.id), msg])
        else: await session.send(msg)
        return
    
    selected_vision_model = None
    selected_text_model = None
    
    if vision_model_val:
        if vision_model_val.lower() == "off":
            selected_vision_model = "off"
        else:
            selected_vision_model, err = resolve_model_name(vision_model_val, conf.models)
            if err:
                if conf.quote: await session.send([Quote(session.event.message.id), err])
                else: await session.send(err)
                return
        logger.info(f"Selected vision model: {selected_vision_model}")
        
    if text_model_val:
        selected_text_model, err = resolve_model_name(text_model_val, conf.models)
        if err:
            if conf.quote: await session.send([Quote(session.event.message.id), err])
            else: await session.send(err)
            return
        logger.info(f"Selected text model: {selected_text_model}")
        
    # Determine History to Continue
    target_key = None
    context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"
    
    # 1. Explicit Code
    if code_val:
        target_code = code_val
        target_key = history_manager.get_key_by_code(target_code)
        if not target_key:
            msg = f"未找到代码为 {target_code} 的会话"
            if conf.quote: await session.send([Quote(session.event.message.id), msg])
            else: await session.send(msg)
            return
        logger.info(f"Question: Continuing session {target_code} -> {target_key}")
                
    next_input_val = args.get("next_input")
    next_text_model = None
    next_vision_model = None
    next_prompt = None
    
    if next_input_val:
        # Parse secondary command
        # next_input_val is likely a MessageChain or string depending on AllParam behavior with Alconna
        # We need to ensure it's a string or compatible input for parse
        logger.info(f"Parsing next input: {next_input_val}")
        try:
            # Convert next_input_val to string
            if isinstance(next_input_val, list):
                # It's a list of segments (e.g. [Text(...)])
                # We need to join them into a string
                # Assuming they are Satori elements or similar
                cmd_str = "".join(str(x) for x in next_input_val)
            else:
                cmd_str = str(next_input_val)
            
            # Prepend 'next' header for Alconna
            parse_target = f"next {cmd_str}"
            
            next_res = next_alc.parse(parse_target)
            if next_res.matched:
                next_args = next_res.all_matched_args
                next_text_model = next_args.get("text_model")
                next_vision_model = next_args.get("vision_model")
                next_prompt = next_args.get("prompt")
                
                # If prompt is AllParam, it might be captured as a list or string depending on Alconna version
                # If it's a list, join it back to string
                if isinstance(next_prompt, list):
                     next_prompt = "".join(str(x) for x in next_prompt)
                
                logger.info(f"Next Command Parsed: text={next_text_model}, vision={next_vision_model}, prompt={next_prompt}")
            else:
                logger.warning(f"Next command parsing failed or no match for: {parse_target}")
                # Fallback: treat the whole string as prompt if parsing failed (e.g. if it didn't match options but Alconna should have matched prompt)
                # But next_alc has Args["prompt", AllParam], so it should match everything else.
                # If it failed, maybe something else is wrong.
                # Let's assume if it failed, we just use the raw string as prompt?
                # But wait, if we prepend "next ", and next_alc starts with "next", it should match.
                pass
        except Exception as e:
            logger.error(f"Failed to parse next command: {e}")

    await process_request(session, args.get("all_param"), selected_model=selected_text_model, selected_vision_model=selected_vision_model, conversation_key_override=target_key, local_mode=local_mode_val, 
                         next_prompt=next_prompt, next_text_model=next_text_model, next_vision_model=next_vision_model)

metadata("hyw", author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}], version="3.2.105", config=HywConfig)

@leto.on(CommandReceive)
async def remove_at(content: MessageChain):
    content = content.lstrip(At)
    return content
