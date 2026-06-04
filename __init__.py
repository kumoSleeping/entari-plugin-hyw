"""entari-plugin-hyw v6."""
from dataclasses import dataclass
from datetime import datetime
import asyncio
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from arclet.alconna import Alconna, AllParam, Args, Arparma
from arclet.entari import (
    At,
    BasicConfModel,
    Image,
    MessageChain,
    MessageCreatedEvent,
    Quote,
    Session,
    Text,
    command,
    listen,
    metadata,
    plugin_config,
)
from arclet.entari.event.command import CommandReceive
from arclet.entari.event.lifespan import Cleanup, Startup
from loguru import logger

from .agent import HywAgent
from .browser.manager import close_shared_browser
from .browser.renderer import get_content_renderer
from .config import HywConfigData
from .flow import FlowRunner
from .prompts import chat_flow
from .render import render as render_card
from .tool_registry import ToolRegistry
from .tools import load_tools, warmup

from .history import HistoryManager
from .misc import parse_color, process_images
from .references import (
    extract_references_from_messages as _extract_references_from_messages,
    format_conversation_log as _format_conversation_log,
)


def format_conversation_log(api_messages: List[Dict[str, Any]]) -> str:
    """
    格式化对话记录，直接保存原始内容
    - user: 用户消息
    - sys: 系统消息（持久）
    - sys_temp: 临时系统消息
    - llm: LLM 回复（assistant）
    - tool: 工具结果
    """
    return _format_conversation_log(api_messages)


def extract_references_from_messages(api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _extract_references_from_messages(api_messages)


def save_conversation_log(api_messages: List[Dict[str, Any]], user_input: str) -> Optional[Path]:
    if not api_messages:
        return None

    log_dir = Path("logs/conversations")
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = re.sub(r"[^\w\u4e00-\u9fff]", "_", user_input[:20]).strip("_")
    filename = f"{timestamp}_{summary}.md"
    filepath = log_dir / filename

    content_parts = [
        "# 对话记录",
        "",
        f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **用户输入**: {user_input}",
        "",
        "---",
        "",
        "## 对话记录",
        "",
        format_conversation_log(api_messages),
    ]

    filepath.write_text("\n".join(content_parts), encoding="utf-8")
    logger.info(f"对话记录已保存: {filepath}")
    return filepath


try:
    from importlib.metadata import version as get_version

    __version__ = get_version("entari_plugin_hyw")
except Exception:
    __version__ = "6.0.2"


@dataclass
class HywConfig(BasicConfModel):
    """Plugin configuration mapping to the HYW runtime."""

    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "gpt-4o"
    temperature: float = 0.5

    question_command: str = "/q"
    web_command: str = "/w"
    headless: bool = False
    quote: bool = False
    theme_color: str = "#ef4444"
    jina_key: str = ""
    show_progress: bool = True

    def to_core_config(self) -> HywConfigData:
        return HywConfigData(
            api_key=os.path.expandvars(str(self.api_key)).strip() if self.api_key else None,
            base_url=os.path.expandvars(str(self.base_url)).strip(),
            model_name=os.path.expandvars(str(self.model_name)).strip(),
            temperature=self.temperature,
            headless=self.headless,
            jina_key=os.path.expandvars(str(self.jina_key)).strip(),
        )


conf = plugin_config(HywConfig)
history_manager = HistoryManager()

_core_agent: Optional[HywAgent] = None
_tool_registry: Optional[ToolRegistry] = None


def get_agent():
    global _core_agent, _tool_registry
    if not _core_agent:
        logger.info("Initializing HYW Agent...")
        core_config = conf.to_core_config()
        if core_config.jina_key:
            os.environ["JINA_API_KEY"] = os.path.expandvars(str(core_config.jina_key)).strip()
        _core_agent = HywAgent(core_config)

        try:
            logger.info("Auto-loading HYW tools...")
            _tool_registry = load_tools(core_config)
            if _tool_registry:
                loaded_names = list(_tool_registry._tools.keys())
                logger.info(f"Tools loaded successfully: {loaded_names}")
        except Exception as e:
            logger.exception(f"Failed to load tools: {e}")
            _tool_registry = ToolRegistry()

    return _core_agent, _tool_registry


def _get_context_id(session: Session[MessageCreatedEvent]) -> str:
    try:
        guild = session.guild
    except RuntimeError:
        guild = None
    except Exception:
        guild = None

    if guild and getattr(guild, "id", None):
        return f"guild_{guild.id}"

    try:
        user = session.user
    except RuntimeError:
        user = None
    except Exception:
        user = None

    if user and getattr(user, "id", None):
        return f"user_{user.id}"

    event_id = getattr(session.event, "id", None)
    return f"event_{event_id or id(session.event)}"


async def _prepare_render_tab(headless: bool) -> Optional[str]:
    try:
        renderer = await get_content_renderer(headless=headless)
        tab_id = await renderer.prepare_tab()
        logger.info(f"Render tab prepared: {tab_id}")
        return tab_id
    except Exception as e:
        logger.warning(f"Render tab prepare failed: {e}")
        return None


async def _reload_and_close_render_tab(headless: bool, tab_id: Optional[str]):
    if not tab_id:
        return
    try:
        renderer = await get_content_renderer(headless=headless)
        await renderer.reload_and_close_tab(tab_id)
    except Exception as e:
        logger.debug(f"Render tab cleanup skipped: {e}")


@listen(CommandReceive)
async def remove_at(content: MessageChain):
    return content.lstrip(At)


alc_q = Alconna(conf.question_command, Args["content;?", AllParam])


@command.on(alc_q)
async def handle_question(session: Session[MessageCreatedEvent], result: Arparma):
    content = result.all_matched_args.get("content")
    mc = MessageChain(content) if content else MessageChain()

    history_manager.prune_expired()

    reply_msg_id = None
    is_reply_to_bot = False
    if session.reply and hasattr(session.reply.origin, "id"):
        reply_msg_id = str(session.reply.origin.id)
        is_reply_to_bot = history_manager.is_bot_message(reply_msg_id)

    if session.reply:
        try:
            if reply_msg_id and not is_reply_to_bot:
                mc.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception:
            if not is_reply_to_bot:
                mc.extend(MessageChain(" ") + session.reply.origin.message)

    user_input = str(mc.get(Text)).strip() if mc.get(Text) else ""
    user_input = re.sub(r"<img[^>]+>", "", user_input, flags=re.IGNORECASE)

    if not user_input and not mc.get(Image):
        return

    prepared_tab_task: Optional[asyncio.Task] = asyncio.create_task(_prepare_render_tab(conf.headless))
    prepared_tab_id: Optional[str] = None

    hist_key = None
    if is_reply_to_bot and reply_msg_id:
        hist_key = history_manager.get_conversation_id(reply_msg_id)
    hist_payload = history_manager.get_history(hist_key) if hist_key else []

    images, _ = await process_images(mc, None)

    agent, registry = get_agent()
    if registry and conf.show_progress:
        registry.set_send_hook(session.send)

    try:
        runner = FlowRunner(agent, registry)
        is_continuation = bool(hist_payload)
        start_turn = 1 if is_continuation else 0
        result = await runner.run(
            user_input,
            flow=chat_flow,
            images=images,
            history_messages=hist_payload if is_continuation else None,
            start_turn=start_turn,
            continuation=is_continuation,
        )

        final_content = (result.content or "").strip()
        should_render = result.should_render
        scoring = result.scoring

        chain = MessageChain()

        if should_render and final_content:
            if prepared_tab_task:
                prepared_tab_id = await prepared_tab_task
                prepared_tab_task = None

            references_internal = []
            if hasattr(runner, "_session") and runner._session:
                references_internal = extract_references_from_messages(runner._session.api_messages)

            logger.info(f"Rendering card (len={len(final_content)}, internal_refs={len(references_internal)})")
            theme_color = parse_color(conf.theme_color)
            render_result = await render_card(
                final_content,
                "Assistant Response",
                headless=conf.headless,
                references=references_internal,
                scoring=scoring,
                theme_color=theme_color,
                prepared_tab_id=prepared_tab_id,
            )

            if hasattr(render_result, "content") and "[IMAGE_BASE64:" in render_result.content:
                match = re.search(r"\[IMAGE_BASE64:\s*([A-Za-z0-9+/=]+)\]", render_result.content)
                if match:
                    chain.append(Image(src=f"data:image/jpeg;base64,{match.group(1)}"))
                else:
                    chain.append(Text(final_content))
            else:
                chain.append(Text(final_content))
        elif final_content:
            chain.append(Text(final_content))

        if chain:
            if conf.quote:
                chain = MessageChain(Quote(session.event.message.id)) + chain

            sent = await session.send(chain)

            sent_id = next((str(e.id) for e in sent if hasattr(e, "id")), None) if sent else None
            msg_id = str(session.event.message.id) if hasattr(session.event, "message") else str(session.event.id)
            context_id = _get_context_id(session)

            system_messages = []
            if hasattr(runner, "_session") and runner._session:
                for msg in runner._session.api_messages:
                    if msg.get("role") == "system" and not msg.get("_temp"):
                        msg_content = msg.get("content", "")
                        if msg_content:
                            system_messages.append({"role": "system", "content": msg_content})

            existing_sys = {m.get("content") for m in hist_payload if m.get("role") == "system"}
            system_messages = [m for m in system_messages if m.get("content") not in existing_sys]

            updated_history = system_messages + hist_payload + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": final_content},
            ]
            history_manager.remember(sent_id, updated_history, [msg_id], {}, context_id)

        if hasattr(runner, "_session") and runner._session:
            save_conversation_log(runner._session.api_messages, user_input)

    except Exception as e:
        logger.exception(f"Error in HYW execution: {e}")
        await session.send(f"执行出错: {e}")
    finally:
        if prepared_tab_task:
            try:
                prepared_tab_id = await prepared_tab_task
            except Exception:
                prepared_tab_id = prepared_tab_id or None
            prepared_tab_task = None

        await _reload_and_close_render_tab(conf.headless, prepared_tab_id)


@listen(Startup)
async def on_startup():
    logger.info("HywPlugin Startup: Pre-loading agent resources...")
    _agent, registry = get_agent()

    logger.info("Warming up browser & renderer...")
    await warmup(headless=conf.headless)

    if registry:
        tools = list(registry._tools.keys())
        logger.success(f"HywPlugin Ready! Loaded tools: {tools}")


@listen(Cleanup)
async def cleanup_resources():
    logger.info("Cleaning up HYW resources...")
    close_shared_browser()

    global _core_agent
    if _core_agent:
        await _core_agent.close()
        _core_agent = None


__plugin__ = metadata("hyw", author=[{"name": "kumo", "email": "dev@example.com"}], version=__version__, config=HywConfig)
