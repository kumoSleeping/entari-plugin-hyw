from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
DATA_URL_PATTERN = re.compile(r"data:image/[a-zA-Z0-9+.\-]+;base64,[A-Za-z0-9+/=\s]+")
LONG_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/=]{300,}")
BASE64_BODY_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
FINALISH_TEXT_RE = re.compile(
    r"(最终|结论|总结|已完成|完成了|全部完成|处理完毕|done|finished|final answer)",
    re.IGNORECASE,
)


ProgressHook = Optional[Callable[[str], Awaitable[None]]]


@dataclass
class CodexExecOptions:
    codex_bin: str = "codex"
    model_name: str = "gpt-5.3-codex"
    reasoning_effort: str = "xhigh"
    timeout_seconds: int = 240
    use_json: bool = True
    skip_git_repo_check: bool = True
    log_all_json_events: bool = False
    log_json_event_max_chars: int = 0
    extra_args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    resume_thread_id: Optional[str] = None


@dataclass
class CodexExecResult:
    content: str
    stdout: str
    stderr: str
    returncode: int
    usage: Dict[str, int] = field(default_factory=dict)
    command: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    thread_id: str = ""


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text or "")


def _redact_large_binary(text: str) -> str:
    if not text:
        return ""
    masked = DATA_URL_PATTERN.sub("[IMAGE_DATA_URL_REDACTED]", text)
    masked = LONG_BASE64_PATTERN.sub(lambda m: f"[BASE64_REDACTED:{len(m.group(0))}]", masked)
    return masked


def _preview_for_log(text: str, max_chars: int = 220) -> str:
    safe = _redact_large_binary(text or "")
    single_line = re.sub(r"\s+", " ", safe).strip()
    if not single_line:
        return ""
    if len(single_line) <= max_chars:
        return single_line
    return f"{single_line[: max_chars - 3]}..."


def _looks_like_raw_base64(payload: str) -> bool:
    text = (payload or "").strip()
    if len(text) < 120:
        return False
    return bool(BASE64_BODY_RE.fullmatch(text))


def _decode_to_image_file(image_input: str, temp_dir: Path, index: int) -> Path:
    if not image_input:
        raise ValueError("empty image input")

    payload = image_input.strip()
    suffix = ".jpg"

    if payload.startswith("data:image/"):
        header, _, payload = payload.partition(",")
        fmt_match = re.search(r"data:image/([a-zA-Z0-9+.-]+);base64", header)
        if fmt_match:
            ext = fmt_match.group(1).lower().replace("jpeg", "jpg")
            suffix = f".{ext}"
        data = base64.b64decode(payload, validate=False)
        image_path = temp_dir / f"codex_image_{index}{suffix}"
        image_path.write_bytes(data)
        return image_path

    if payload.startswith("base64://"):
        payload = payload[len("base64://") :].strip()
        data = base64.b64decode(payload, validate=False)
        image_path = temp_dir / f"codex_image_{index}{suffix}"
        image_path.write_bytes(data)
        return image_path

    if _looks_like_raw_base64(payload):
        data = base64.b64decode(payload, validate=False)
        image_path = temp_dir / f"codex_image_{index}{suffix}"
        image_path.write_bytes(data)
        return image_path

    candidate = Path(payload)
    try:
        if candidate.exists():
            return candidate
    except OSError as exc:
        raise ValueError(f"invalid image path input: {exc}") from exc

    raise ValueError("image input is neither existing file path nor decodable base64")


def _parse_json_line(line: str) -> Optional[Dict[str, Any]]:
    stripped = (line or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _collect_text_candidates(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, str):
        text = obj.strip()
        if text:
            out.append(text)
        return out
    if isinstance(obj, list):
        for item in obj:
            out.extend(_collect_text_candidates(item))
        return out
    if isinstance(obj, dict):
        for key in ("last_message", "output_text", "text", "content", "message", "delta"):
            if key in obj:
                out.extend(_collect_text_candidates(obj[key]))
        return out
    return out


def _extract_agent_message_text(event: Dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return ""
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    if str(item.get("type", "")).lower() != "agent_message":
        return ""

    candidates = _collect_text_candidates(item)
    if not candidates:
        return ""

    deduped: List[str] = []
    seen: set[str] = set()
    for text in candidates:
        stripped = text.strip()
        if not stripped:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        deduped.append(stripped)
    return "\n".join(deduped).strip()


def _extract_content_from_events(events: List[Dict[str, Any]]) -> str:
    for event in reversed(events):
        message_text = _extract_agent_message_text(event)
        if message_text:
            return message_text
    for event in reversed(events):
        for text in _collect_text_candidates(event):
            if text.startswith("{") and text.endswith("}"):
                continue
            if len(text) < 2:
                continue
            return text
    return ""


def _extract_content_from_stdout(stdout_text: str) -> str:
    lines = []
    for raw in (stdout_text or "").splitlines():
        line = _strip_ansi(raw).strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            continue
        if line.startswith("WARNING:"):
            continue
        lines.append(line)
    return "\n".join(lines[-40:]).strip() if lines else ""


def _extract_user_facing_content(content: str) -> str:
    """Normalize final text to avoid leaking internal planning blocks."""
    if not content:
        return ""

    without_scoring = re.sub(r"<scoring>.*?</scoring>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    if not without_scoring:
        return ""

    execution_matches = re.findall(
        r"<execution_content[^>]*>(.*?)</execution_content>",
        without_scoring,
        flags=re.DOTALL | re.IGNORECASE,
    )
    for block in execution_matches:
        candidate = block.strip()
        if candidate:
            return candidate

    without_logic = re.sub(
        r"<response_logic[^>]*>.*?</response_logic>",
        "",
        without_scoring,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    if without_logic:
        return without_logic

    without_internal_blocks = re.sub(
        r"<(?:planning|clarification_needed|vision_analysis)[^>]*>.*?</(?:planning|clarification_needed|vision_analysis)>",
        "",
        without_scoring,
        flags=re.DOTALL | re.IGNORECASE,
    )
    without_internal_tags = re.sub(
        r"</?(?:planning|clarification_needed|vision_analysis|execution_content|response_logic)[^>]*>",
        "",
        without_internal_blocks,
        flags=re.IGNORECASE,
    ).strip()
    if without_internal_tags:
        return without_internal_tags

    return without_scoring


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_usage_from_events(events: List[Dict[str, Any]]) -> Dict[str, int]:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("type", "")).lower() != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = _coerce_int(usage.get("input_tokens"))
        cached_input_tokens = _coerce_int(usage.get("cached_input_tokens"))
        output_tokens = _coerce_int(usage.get("output_tokens"))
        total_tokens = _coerce_int(usage.get("total_tokens")) or (input_tokens + output_tokens)
        return {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    return {}


def _extract_thread_id_from_events(events: List[Dict[str, Any]]) -> str:
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type", "")).lower() != "thread.started":
            continue
        thread_id = str(event.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
    return ""


def _has_completion_event(events: List[Dict[str, Any]]) -> bool:
    completion_types = {
        "turn.completed",
        "response.completed",
        "thread.completed",
        "session.completed",
    }
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type", "")).lower() in completion_types:
            return True
    return False


def _looks_like_final_reply_text(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return False
    if len(content) >= 220:
        return True
    if FINALISH_TEXT_RE.search(content):
        return True
    if "```" in content:
        return True
    if re.search(r"^#{1,6}\s", content, re.MULTILINE):
        return True
    if re.search(r"^\d+\.\s", content, re.MULTILINE):
        return True
    return False


def _event_looks_final(event: Dict[str, Any]) -> bool:
    if not isinstance(event, dict):
        return False

    event_type = str(event.get("type", "")).lower()
    if event_type in {
        "turn.completed",
        "thread.completed",
        "response.completed",
        "session.completed",
        "error",
    }:
        return True

    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type", "")).lower()
        if item_type == "agent_message":
            message_text = _extract_agent_message_text(event)
            return _looks_like_final_reply_text(message_text)
    return False


async def run_codex_exec(
    prompt: str,
    images: Optional[List[str]] = None,
    options: Optional[CodexExecOptions] = None,
    progress_hook: ProgressHook = None,
) -> CodexExecResult:
    if options is None:
        options = CodexExecOptions()

    with tempfile.TemporaryDirectory(prefix="hyw_codex_exec_") as tmp:
        tmp_dir = Path(tmp)
        output_file = tmp_dir / "last_message.txt"
        image_paths: List[Path] = []

        for idx, image in enumerate(images or []):
            try:
                image_path = _decode_to_image_file(str(image), tmp_dir, idx)
                image_paths.append(image_path)
            except Exception as exc:
                logger.warning(f"Ignore invalid image input #{idx}: {_redact_large_binary(str(exc))}")

        is_resume = bool((options.resume_thread_id or "").strip())
        if is_resume:
            cmd: List[str] = [
                options.codex_bin,
                "exec",
                "resume",
                "-c",
                f'model_reasoning_effort="{options.reasoning_effort}"',
            ]
        else:
            cmd = [
                options.codex_bin,
                "exec",
                "-c",
                f'model_reasoning_effort="{options.reasoning_effort}"',
            ]
        if options.skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        cmd.extend(["-m", options.model_name])
        if options.use_json:
            cmd.append("--json")
        if not is_resume:
            cmd.extend(["-o", str(output_file)])
        if options.extra_args:
            cmd.extend(str(arg) for arg in options.extra_args if str(arg).strip())
        for image_path in image_paths:
            cmd.extend(["-i", str(image_path)])

        # Place prompt after `--` so leading "-" content is never parsed as CLI flags.
        if is_resume:
            cmd.extend(["--", str(options.resume_thread_id).strip(), prompt])
        else:
            cmd.extend(["--", prompt])

        if is_resume:
            thread_preview = str(options.resume_thread_id or "").strip()
            if len(thread_preview) > 16:
                thread_preview = f"{thread_preview[:8]}...{thread_preview[-4:]}"
            cmd_preview = " ".join(shlex.quote(part) for part in cmd[:-2])
            cmd_preview = f"{cmd_preview} <thread:{thread_preview}> <prompt:{len(prompt)} chars>"
        else:
            cmd_preview = " ".join(shlex.quote(part) for part in cmd[:-1])
            cmd_preview = f"{cmd_preview} <prompt:{len(prompt)} chars>"
        logger.info("Codex exec command: {}", cmd_preview)

        logger.info(
            "Codex exec start: model={}, images={}, cwd={}",
            options.model_name,
            len(image_paths),
            options.cwd or ".",
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=options.cwd or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        events: List[Dict[str, Any]] = []
        agent_msg_seq = 0
        last_agent_message = ""
        pending_progress_message: Optional[str] = None

        async def _consume_stdout() -> None:
            nonlocal agent_msg_seq, last_agent_message, pending_progress_message
            if proc.stdout is None:
                return
            while True:
                line_b = await proc.stdout.readline()
                if not line_b:
                    break
                line = _strip_ansi(line_b.decode("utf-8", errors="replace")).rstrip("\n")
                stdout_lines.append(line)
                parsed = _parse_json_line(line) if options.use_json else None
                if options.use_json and options.log_all_json_events and line.strip().startswith("{") and parsed is None:
                    raw_line = _redact_large_binary(line)
                    max_chars = max(int(options.log_json_event_max_chars or 0), 0)
                    if max_chars and len(raw_line) > max_chars:
                        raw_line = f"{raw_line[: max_chars - 3]}..."
                    logger.info("Codex JSON raw line (unparsed): {}", raw_line)
                if parsed:
                    events.append(parsed)
                    if options.log_all_json_events:
                        event_type = str(parsed.get("type", "")).strip() or "-"
                        event_payload = _redact_large_binary(
                            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
                        )
                        max_chars = max(int(options.log_json_event_max_chars or 0), 0)
                        if max_chars and len(event_payload) > max_chars:
                            event_payload = f"{event_payload[: max_chars - 3]}..."
                        logger.info(
                            "Codex JSON event #{} [{}]: {}",
                            len(events),
                            event_type,
                            event_payload,
                        )
                    if pending_progress_message:
                        if _event_looks_final(parsed):
                            logger.debug("Skip pending agent_message due to final-like next event.")
                            pending_progress_message = None
                        elif progress_hook is not None:
                            try:
                                display_progress_message = _extract_user_facing_content(pending_progress_message)
                                if display_progress_message:
                                    await progress_hook(display_progress_message)
                            except Exception:
                                pass
                            pending_progress_message = None

                    message = _extract_agent_message_text(parsed)
                    if message:
                        agent_msg_seq += 1
                        preview = _preview_for_log(message, max_chars=260)
                        logger.info(
                            "Codex return #{} preview(len={}): {}",
                            agent_msg_seq,
                            len(message),
                            preview,
                        )
                        last_agent_message = message
                        pending_progress_message = message

        async def _consume_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                line_b = await proc.stderr.readline()
                if not line_b:
                    break
                line = _strip_ansi(line_b.decode("utf-8", errors="replace")).rstrip("\n")
                stderr_lines.append(line)

        stdout_task = asyncio.create_task(_consume_stdout())
        stderr_task = asyncio.create_task(_consume_stderr())

        try:
            await asyncio.wait_for(proc.wait(), timeout=max(int(options.timeout_seconds), 30))
            await stdout_task
            await stderr_task
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            await stdout_task
            await stderr_task
            raise TimeoutError(f"codex exec timed out after {options.timeout_seconds}s") from exc

        stdout_text = "\n".join(stdout_lines).strip()
        stderr_text = "\n".join(stderr_lines).strip()
        usage = _extract_usage_from_events(events)
        event_thread_id = _extract_thread_id_from_events(events)
        resolved_thread_id = event_thread_id or str(options.resume_thread_id or "").strip()

        content = ""
        content_source = ""
        if output_file.exists():
            content = output_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                content_source = "output_file"
        if not content:
            content = _extract_content_from_events(events)
            if content:
                content_source = "events"
        if not content:
            content = _extract_content_from_stdout(stdout_text)
            if content:
                content_source = "stdout"
        if not content and last_agent_message:
            content = last_agent_message.strip()
            if content:
                content_source = "agent_message"
        completed = _has_completion_event(events)
        if options.use_json and not completed and content_source != "output_file":
            if content:
                logger.warning(
                    "Codex turn incomplete (no completion event), ignore partial content from {} (len={}).",
                    content_source or "unknown",
                    len(content or ""),
                )
            content = ""

        if content:
            content = _extract_user_facing_content(content)

        if proc.returncode != 0:
            detail_source = stderr_text or stdout_text or content or f"exit code {proc.returncode}"
            detail = _redact_large_binary(detail_source)[-1000:]
            raise RuntimeError(f"codex exec failed: {detail}")

        if (
            pending_progress_message
            and progress_hook is not None
            and pending_progress_message.strip()
            and pending_progress_message.strip() != (content or "").strip()
        ):
            try:
                display_progress_message = _extract_user_facing_content(pending_progress_message)
                if display_progress_message and display_progress_message.strip() != (content or "").strip():
                    await progress_hook(display_progress_message)
            except Exception:
                pass
            finally:
                pending_progress_message = None

        logger.info(
            "Codex exec done: returncode={}, agent_returns={}, final_len={}, thread_id={}",
            proc.returncode,
            agent_msg_seq,
            len(content or ""),
            resolved_thread_id or "-",
        )
        if content:
            logger.info(
                "Codex final return preview(len={}): {}",
                len(content),
                _preview_for_log(content, max_chars=320),
            )
        if usage:
            logger.info(
                "Codex usage: input={}, cached_input={}, output={}, total={}",
                usage.get("input_tokens", 0),
                usage.get("cached_input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("total_tokens", 0),
            )

        return CodexExecResult(
            content=content,
            stdout=_redact_large_binary(stdout_text),
            stderr=_redact_large_binary(stderr_text),
            returncode=proc.returncode,
            usage=usage,
            command=cmd,
            events=events,
            thread_id=resolved_thread_id,
        )
