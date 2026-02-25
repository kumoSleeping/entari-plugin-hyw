"""
entari-plugin-codex - Codex native execution plugin
"""
import asyncio
import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from shutil import which
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

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

from .codex_exec import CodexExecOptions, run_codex_exec
from .drission_render import close_drission_renderer, render_markdown_to_base64_drission, warmup_drission_renderer
from .history import HistoryManager
from .misc import compress_image_b64, process_images
from .policies import build_codex_exec_prompt
from .simple_render import render_markdown_to_base64


def format_conversation_log(api_messages: List[Dict[str, Any]]) -> str:
    blocks = []
    for msg in api_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    parts.append("[IMAGE]")
                else:
                    parts.append(str(part))
            content = " ".join(parts)

        tag = "user" if role == "user" else "assistant" if role == "assistant" else "sys"
        blocks.append(f"```{tag}\n{content}\n```")

    return "\n\n".join(blocks)


def save_conversation_log(
    api_messages: List[Dict[str, Any]],
    user_input: str,
    workspace_dir: Optional[str] = None,
) -> Optional[Path]:
    if not api_messages:
        return None

    base_dir = Path(workspace_dir).expanduser() if workspace_dir else Path(".")
    log_dir = base_dir / "logs" / "conversations"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = re.sub(r"[^\w\u4e00-\u9fff]", "_", (user_input or "image_task")[:20]).strip("_")
    filename = f"{timestamp}_{summary or 'codex'}.md"
    filepath = log_dir / filename

    content_parts = [
        "# 对话记录",
        "",
        f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **用户输入**: {user_input or '[仅图片输入]'}",
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

    __version__ = get_version("entari_plugin_codex")
except Exception:
    __version__ = "0.0.1"


@dataclass
class CodexConfig(BasicConfModel):
    question_command: str = "/c"
    quote: bool = False
    render_image: bool = True
    theme_color: str = "#ef4444"

    codex_bin: str = "codex"
    codex_model: str = "gpt-5.3-codex"
    codex_reasoning_effort: str = "medium"
    codex_timeout_seconds: int = 240
    codex_use_json: bool = True
    codex_skip_git_repo_check: bool = True
    codex_log_all_json_events: bool = True
    codex_log_json_event_max_chars: int = 0
    codex_workdir: str = "./hyw-workspace"
    codex_per_user_workdir: bool = True
    cache_user_images: bool = True
    cache_user_images_dir: str = ".codex/input_images"
    codex_extra_args: str = ""
    reply_image_max_bytes: int = 2_400_000
    history_ttl_seconds: int = 7 * 24 * 3600
    history_state_file: str = ""


conf = plugin_config(CodexConfig)
history_manager = HistoryManager(ttl_seconds=max(int(conf.history_ttl_seconds), 60))
_shutdown_requested = False
_active_request_tasks: set[asyncio.Task[Any]] = set()


def _history_state_path() -> Path:
    configured = str(conf.history_state_file or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(conf.codex_workdir or ".").expanduser().resolve()
    return (base / ".codex" / "history_state.json").resolve()


def _parse_codex_extra_args(raw: str) -> List[str]:
    if not raw:
        return []
    parsed: List[str] = []
    for item in str(raw).split(","):
        token = item.strip()
        if not token:
            continue
        if token in {"-", "--"}:
            logger.warning("Ignore invalid codex_extra_args token: {}", token)
            continue
        parsed.append(token)
    return parsed


def _build_codex_options(
    *,
    cwd: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    resume_thread_id: Optional[str] = None,
) -> CodexExecOptions:
    resolved_effort = _normalize_reasoning_effort(reasoning_effort or conf.codex_reasoning_effort)
    return CodexExecOptions(
        codex_bin=conf.codex_bin,
        model_name=conf.codex_model or "gpt-5.3-codex",
        reasoning_effort=resolved_effort,
        timeout_seconds=max(int(conf.codex_timeout_seconds), 30),
        use_json=bool(conf.codex_use_json),
        skip_git_repo_check=bool(conf.codex_skip_git_repo_check),
        log_all_json_events=bool(conf.codex_log_all_json_events),
        log_json_event_max_chars=max(int(conf.codex_log_json_event_max_chars), 0),
        extra_args=_parse_codex_extra_args(conf.codex_extra_args),
        cwd=str((cwd or conf.codex_workdir or ".")).strip() or ".",
        resume_thread_id=(str(resume_thread_id).strip() if resume_thread_id else None),
    )


def _contains_markdown(text: str) -> bool:
    if not text:
        return False
    patterns = [
        r"^#{1,6}\s",
        r"^[\*\-]\s",
        r"^\d+\.\s",
        r"```",
        r"`[^`]+`",
        r"\[.*?\]\(.*?\)",
        r"(\*\*|__|\*|_).*(\*\*|__|\*|_)",
        r"^>\s",
        r"^---\s*$",
        r"\|.*\|",
    ]
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def _should_render_output(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return False
    if len(content) >= 220:
        return True
    return _contains_markdown(content)


def _format_usage_line(usage: Optional[Dict[str, Any]]) -> str:
    if not isinstance(usage, dict) or not usage:
        return ""
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_input_tokens = int(usage.get("cached_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return (
        f"Token 消耗: input={input_tokens}, cached_input={cached_input_tokens}, "
        f"output={output_tokens}, total={total_tokens}"
    )


def _build_runtime_stats(
    usage: Optional[Dict[str, Any]],
    elapsed_seconds: float,
    operation_rounds: int,
) -> Dict[str, Any]:
    usage_payload = usage or {}
    return {
        "usage": {
            "input_tokens": int(usage_payload.get("input_tokens") or 0),
            "cached_input_tokens": int(usage_payload.get("cached_input_tokens") or 0),
            "output_tokens": int(usage_payload.get("output_tokens") or 0),
            "total_tokens": int(usage_payload.get("total_tokens") or 0),
        },
        "total_time": round(max(float(elapsed_seconds), 0.0), 3),
        "operation_rounds": max(int(operation_rounds), 0),
    }


def _count_operation_rounds(events: List[Dict[str, Any]]) -> int:
    if not events:
        return 0

    turn_completed = sum(1 for event in events if str(event.get("type", "")).lower() == "turn.completed")
    if turn_completed > 0:
        return turn_completed

    agent_messages = 0
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and str(item.get("type", "")).lower() == "agent_message":
            agent_messages += 1
    return agent_messages


_REASONING_FLAG_TO_EFFORT: Dict[str, str] = {
    "-l": "low",
    "-m": "medium",
    "-h": "high",
    "-x": "xhigh",
}
_VALID_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def _normalize_reasoning_effort(raw: Optional[str], default: str = "medium") -> str:
    effort = str(raw or "").strip().lower()
    if effort in _VALID_REASONING_EFFORTS:
        return effort
    return default


def _extract_reasoning_mode(user_input: str) -> Tuple[str, str, str]:
    text = (user_input or "").strip()
    default_effort = _normalize_reasoning_effort(conf.codex_reasoning_effort, default="medium")

    if not text:
        for flag, effort in _REASONING_FLAG_TO_EFFORT.items():
            if effort == default_effort:
                return "", effort, flag
        return "", default_effort, "-m"

    first, _, rest = text.partition(" ")
    mapped = _REASONING_FLAG_TO_EFFORT.get(first)
    if mapped:
        return rest.strip(), mapped, first

    for flag, effort in _REASONING_FLAG_TO_EFFORT.items():
        if effort == default_effort:
            return text, effort, flag
    return text, default_effort, "-m"


def _safe_workspace_segment(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", (raw or "").strip())
    return cleaned or "unknown_user"


def _resolve_actor_id(session: Session[MessageCreatedEvent]) -> str:
    event_member = getattr(session.event, "member", None)
    if event_member:
        member_user = getattr(event_member, "user", None)
        member_user_id = getattr(member_user, "id", None)
        if member_user_id:
            return str(member_user_id)

    event_user = getattr(session.event, "user", None)
    event_user_id = getattr(event_user, "id", None)
    if event_user_id:
        return str(event_user_id)

    try:
        return str(session.user.id)
    except Exception:
        return "unknown_user"


def _workspace_for_user(user_id: str) -> Path:
    base = Path(conf.codex_workdir or ".").expanduser()
    if conf.codex_per_user_workdir:
        base = base / _safe_workspace_segment(user_id)
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def _resolve_input_image_cache_dir(workspace_dir: Path) -> Path:
    configured = str(conf.cache_user_images_dir or "").strip()
    if configured:
        cache_dir = Path(configured).expanduser()
        if not cache_dir.is_absolute():
            cache_dir = workspace_dir / cache_dir
    else:
        cache_dir = workspace_dir / ".codex" / "input_images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir.resolve()


def _decode_image_payload(image_input: str) -> Tuple[Optional[bytes], str]:
    payload = (image_input or "").strip()
    if not payload:
        return None, ".jpg"

    suffix = ".jpg"

    if payload.startswith("data:image/"):
        header, _, payload = payload.partition(",")
        fmt_match = re.search(r"data:image/([a-zA-Z0-9+.-]+);base64", header)
        if fmt_match:
            ext = fmt_match.group(1).lower().replace("jpeg", "jpg")
            suffix = f".{ext}"
    elif payload.startswith("base64://"):
        payload = payload[len("base64://") :].strip()
    else:
        local_path = Path(payload).expanduser()
        try:
            if local_path.exists() and local_path.is_file():
                suffix = local_path.suffix.lower() or ".jpg"
                if suffix == ".jpeg":
                    suffix = ".jpg"
                return local_path.read_bytes(), suffix
        except OSError:
            return None, ".jpg"
        if len(payload) < 64 or not re.fullmatch(r"[A-Za-z0-9+/=\s]+", payload):
            return None, ".jpg"

    try:
        image_bytes = base64.b64decode(payload, validate=False)
    except Exception:
        return None, suffix

    if not image_bytes:
        return None, suffix
    if suffix == ".jpeg":
        suffix = ".jpg"
    return image_bytes, suffix


def _cache_user_images_for_request(images: List[str], workspace_dir: Path, request_id: str) -> List[str]:
    if not images:
        return []

    cache_root = _resolve_input_image_cache_dir(workspace_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_request_id = _safe_workspace_segment(request_id) or "unknown_request"
    request_dir = cache_root / f"{stamp}_{safe_request_id}"
    request_dir.mkdir(parents=True, exist_ok=True)

    cached_images: List[str] = []
    for idx, image in enumerate(images, start=1):
        image_bytes, suffix = _decode_image_payload(str(image))
        if not image_bytes:
            logger.warning("Skip caching invalid user image #{} for request {}", idx, safe_request_id)
            cached_images.append(str(image))
            continue

        digest = hashlib.sha256(image_bytes).hexdigest()[:12]
        filename = f"img_{idx:02d}_{digest}{suffix}"
        output_path = request_dir / filename
        try:
            output_path.write_bytes(image_bytes)
            cached_images.append(str(output_path))
            logger.info(
                "Cached user image #{} -> {} ({} KB)",
                idx,
                output_path,
                len(image_bytes) // 1024,
            )
        except OSError as exc:
            logger.warning("Failed to cache user image #{} to {}: {}", idx, output_path, exc)
            cached_images.append(str(image))

    return cached_images


@listen(CommandReceive)
async def remove_at(content: MessageChain):
    return content.lstrip(At)


alc_q = Alconna(conf.question_command, Args["content;?", AllParam])


@command.on(alc_q)
async def handle_question(session: Session[MessageCreatedEvent], result: Arparma):
    current_task = asyncio.current_task()
    if current_task is not None:
        _active_request_tasks.add(current_task)
    try:
        if _shutdown_requested:
            logger.info("Skip Codex request because shutdown is in progress.")
            return

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

        hist_key = None
        if is_reply_to_bot and reply_msg_id:
            hist_key = history_manager.get_conversation_id(reply_msg_id)
        hist_payload = history_manager.get_history(hist_key) if hist_key else []
        hist_meta = history_manager.get_metadata(hist_key) if hist_key else {}
        resume_thread_id = str(hist_meta.get("codex_thread_id") or "").strip()

        actor_user_id = _resolve_actor_id(session)
        owner_user_id = str(hist_meta.get("owner_user_id") or actor_user_id)
        if hist_meta.get("conversation_workdir"):
            conversation_workdir = Path(str(hist_meta["conversation_workdir"])).expanduser()
            conversation_workdir.mkdir(parents=True, exist_ok=True)
            conversation_workdir = conversation_workdir.resolve()
        else:
            conversation_workdir = _workspace_for_user(owner_user_id)
        request_msg_id = (
            str(session.event.message.id)
            if hasattr(session.event, "message") and hasattr(session.event.message, "id")
            else str(getattr(session.event, "id", "unknown_message"))
        )

        images, _ = await process_images(mc, None)
        if images and bool(conf.cache_user_images):
            images = _cache_user_images_for_request(
                images,
                workspace_dir=conversation_workdir,
                request_id=request_msg_id,
            )

        user_input, reasoning_effort, reasoning_flag = _extract_reasoning_mode(user_input)

        if not user_input and not mc.get(Image) and not hist_payload:
            return

        if user_input:
            task_text = user_input
        elif images:
            task_text = "请结合图片与上下文完成用户任务。"
        elif hist_payload:
            task_text = "请继续上一轮任务并给出可交付结果。"
        else:
            task_text = "请完成用户任务。"
        summary = "已完成输入解析，开始由 Codex 接管执行。"

        context_bits: List[str] = []
        if hist_payload:
            context_bits.append("这是延续对话，请参考最近上下文。")
        if images:
            context_bits.append("用户提供了图片输入，请结合图片内容处理。")
        context_bits.append(f"本轮思考强度模式: {reasoning_flag} ({reasoning_effort})。")
        context_text = " ".join(context_bits) if context_bits else "请直接执行并给出可交付结果。"

        logger.info(
            "Codex route: actor={}, owner={}, reasoning={}({}), workspace={}, resume_thread={}",
            actor_user_id,
            owner_user_id,
            reasoning_flag,
            reasoning_effort,
            conversation_workdir,
            resume_thread_id or "-",
        )

        prompt_history = [] if resume_thread_id else hist_payload
        prompt = build_codex_exec_prompt(
            raw_user_input=user_input,
            handoff_summary=summary,
            task=task_text,
            context=context_text,
            history_messages=prompt_history,
        )

        latest_progress_message = ""

        async def _send_progress_update(message: str) -> None:
            nonlocal latest_progress_message
            progress_text = (message or "").strip()
            if not progress_text or _shutdown_requested:
                return
            latest_progress_message = progress_text
            try:
                await session.send(MessageChain(Text(progress_text)))
            except Exception as progress_exc:
                logger.debug("Codex progress send failed: {}", progress_exc)

        run_started_tick = monotonic()
        runtime_stats: Dict[str, Any] = {}
        usage_payload: Dict[str, Any] = {}
        try:
            codex_result = await run_codex_exec(
                prompt=prompt,
                images=images,
                options=_build_codex_options(
                    cwd=str(conversation_workdir),
                    reasoning_effort=reasoning_effort,
                    resume_thread_id=resume_thread_id or None,
                ),
                progress_hook=_send_progress_update,
            )
            resolved_thread_id = str(codex_result.thread_id or resume_thread_id or "").strip()
            final_content = (codex_result.content or "").strip()
            usage_payload = codex_result.usage or {}
            operation_rounds = _count_operation_rounds(codex_result.events)
            runtime_stats = _build_runtime_stats(
                usage=usage_payload,
                elapsed_seconds=monotonic() - run_started_tick,
                operation_rounds=operation_rounds,
            )
            usage_line = _format_usage_line(usage_payload)
            if usage_line:
                logger.info("{}", usage_line)
            if not final_content and latest_progress_message:
                final_content = latest_progress_message
            if not final_content:
                final_content = "Codex 未返回有效结果，请稍后重试。"
        except asyncio.CancelledError:
            logger.info(
                "Codex request cancelled: actor={}, workspace={}",
                actor_user_id,
                conversation_workdir,
            )
            return
        except Exception as e:
            logger.exception(f"Error in Codex execution: {e}")
            resolved_thread_id = resume_thread_id
            final_content = f"Codex 执行出错: {e}"
            runtime_stats = _build_runtime_stats(
                usage={},
                elapsed_seconds=monotonic() - run_started_tick,
                operation_rounds=0,
            )

        if _shutdown_requested:
            logger.info("Skip Codex reply send because shutdown is in progress.")
            return

        should_render = bool(conf.render_image) and _should_render_output(final_content)
        chain = MessageChain()
        used_image_reply = False

        if should_render and final_content:
            try:
                image_b64 = await render_markdown_to_base64_drission(
                    final_content,
                    title="Codex",
                    theme_color=conf.theme_color,
                    stats=runtime_stats,
                    total_time=runtime_stats.get("total_time", 0),
                    headless=True,
                )
                image_bytes = (len(image_b64) * 3) // 4
                logger.info("Codex reply image prepared (drission): {} KB", image_bytes // 1024)

                max_bytes = max(int(conf.reply_image_max_bytes), 0)
                if max_bytes and image_bytes > max_bytes:
                    logger.warning(
                        "Codex reply image too large: {} KB > limit {} KB, applying extra compression...",
                        image_bytes // 1024,
                        max_bytes // 1024,
                    )
                    image_b64 = compress_image_b64(image_b64, quality=82, max_width=1600)
                    image_bytes = (len(image_b64) * 3) // 4
                    logger.info("Codex reply image after extra compression: {} KB", image_bytes // 1024)

                if max_bytes and image_bytes > max_bytes:
                    logger.warning(
                        "Codex reply image still too large: {} KB > limit {} KB, fallback to text.",
                        image_bytes // 1024,
                        max_bytes // 1024,
                    )
                    chain.append(Text(final_content))
                else:
                    chain.append(Image(src=f"data:image/jpeg;base64,{image_b64}"))
                    used_image_reply = True
            except Exception as exc:
                logger.warning(f"drission render failed, fallback to simple renderer: {exc}")
                try:
                    image_b64 = render_markdown_to_base64(
                        final_content,
                        title="Codex",
                        theme_color=conf.theme_color,
                        model_name=conf.codex_model,
                        cwd_hint=str(conversation_workdir),
                    )
                    image_bytes = (len(image_b64) * 3) // 4
                    logger.info("Codex reply image prepared (simple): {} KB", image_bytes // 1024)

                    max_bytes = max(int(conf.reply_image_max_bytes), 0)
                    if max_bytes and image_bytes > max_bytes:
                        logger.warning(
                            "Codex reply image (simple) too large: {} KB > limit {} KB, fallback to text.",
                            image_bytes // 1024,
                            max_bytes // 1024,
                        )
                        chain.append(Text(final_content))
                    else:
                        chain.append(Image(src=f"data:image/jpeg;base64,{image_b64}"))
                        used_image_reply = True
                except Exception as fallback_exc:
                    logger.warning(f"simple renderer failed, fallback text: {fallback_exc}")
                    chain.append(Text(final_content))
        elif final_content:
            chain.append(Text(final_content))

        if not chain:
            return

        if conf.quote:
            chain = MessageChain(Quote(session.event.message.id)) + chain

        try:
            sent = await session.send(chain)
            if used_image_reply and not sent and final_content:
                logger.warning(
                    "Codex image send returned empty result; retrying plain text fallback.",
                )
                sent = await session.send(MessageChain(Text(final_content)))
                used_image_reply = False
            if not sent:
                logger.warning("Codex reply send returned empty result (kind={}).", "image" if used_image_reply else "text")
        except Exception as send_exc:
            logger.warning("Codex reply send failed (kind={}): {}", "image" if used_image_reply else "text", send_exc)
            if used_image_reply and final_content:
                try:
                    sent = await session.send(MessageChain(Text(final_content)))
                    used_image_reply = False
                    logger.warning("Codex reply fell back to text send after image send failure.")
                except Exception as text_send_exc:
                    logger.exception(f"Codex text fallback send also failed: {text_send_exc}")
                    return
            else:
                logger.exception(f"Codex send failed: {send_exc}")
                return

        if _shutdown_requested:
            logger.info("Skip Codex history update because shutdown is in progress.")
            return

        sent_id = next((str(e.id) for e in sent if hasattr(e, "id")), None) if sent else None
        logger.info("Codex reply sent: kind={}, sent_id={}", "image" if used_image_reply else "text", sent_id or "-")
        msg_id = str(session.event.message.id) if hasattr(session.event, "message") else str(session.event.id)
        event_guild = getattr(session.event, "guild", None)
        context_id = f"guild_{event_guild.id}:owner_{owner_user_id}" if event_guild else f"user_{owner_user_id}"

        updated_history = hist_payload + [
            {"role": "user", "content": user_input or "[仅图片输入]"},
            {"role": "assistant", "content": final_content},
        ]
        history_manager.remember(
            sent_id,
            updated_history,
            [msg_id],
            {
                "owner_user_id": owner_user_id,
                "conversation_workdir": str(conversation_workdir),
                "reasoning_effort": reasoning_effort,
                "codex_thread_id": resolved_thread_id,
            },
            context_id,
        )
        state_path = _history_state_path()
        if not history_manager.save_state_file(str(state_path)):
            logger.warning("Failed to persist Codex history state: {}", state_path)
        save_conversation_log(
            updated_history,
            user_input or "[仅图片输入]",
            workspace_dir=str(conversation_workdir),
        )
    finally:
        if current_task is not None:
            _active_request_tasks.discard(current_task)


@listen(Startup)
async def on_startup():
    global _shutdown_requested
    _shutdown_requested = False
    _active_request_tasks.clear()
    logger.info("CodexPlugin Startup")
    history_manager.set_ttl_seconds(max(int(conf.history_ttl_seconds), 60))
    state_path = _history_state_path()
    if history_manager.load_state_file(str(state_path)):
        logger.info(
            "Codex history restored: conversations={}, state_file={}",
            history_manager.conversation_count(),
            state_path,
        )
    else:
        logger.info("Codex history state not loaded (missing or invalid): {}", state_path)
    if which(conf.codex_bin):
        logger.success(f"Codex binary ready: {conf.codex_bin}")
    else:
        logger.warning(f"Codex binary missing: {conf.codex_bin}")
    if conf.render_image:
        try:
            logger.info("Warming up DrissionPage renderer (headless)...")
            await warmup_drission_renderer(headless=True)
            logger.success("DrissionPage renderer warmup completed.")
        except Exception as exc:
            logger.warning(f"DrissionPage warmup failed, rendering may fallback: {exc}")


@listen(Cleanup)
async def cleanup_resources():
    global _shutdown_requested
    _shutdown_requested = True
    pending_tasks = [task for task in list(_active_request_tasks) if not task.done()]
    if pending_tasks:
        logger.info("Cancelling {} active Codex request task(s)...", len(pending_tasks))
        for task in pending_tasks:
            task.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    _active_request_tasks.clear()

    logger.info("Cleaning up Codex plugin resources...")
    state_path = _history_state_path()
    if history_manager.save_state_file(str(state_path)):
        logger.info(
            "Codex history saved: conversations={}, state_file={}",
            history_manager.conversation_count(),
            state_path,
        )
    else:
        logger.warning("Failed to save Codex history state during cleanup: {}", state_path)
    await close_drission_renderer()


__plugin__ = metadata("codex", author=[{"name": "kumo", "email": "dev@example.com"}], version=__version__, config=CodexConfig)
