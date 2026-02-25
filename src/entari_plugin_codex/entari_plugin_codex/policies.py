from __future__ import annotations

from typing import Any, Dict, List


CODEX_EXEC_PROMPT = """
- 需要知识时尽可能使用网络搜索获取信息
- 任务结束的最后一轮输出请详细一些
- PS: 用户屏幕较窄减少使用大量文字在表格中
""".strip()


def _normalize_content(content: Any) -> str:
    if isinstance(content, list):
        text_parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") == "image_url":
                text_parts.append("[IMAGE]")
            else:
                text_parts.append(str(part))
        return " ".join(text_parts).strip()
    return str(content).strip()


def build_history_excerpt(history_messages: List[Dict[str, Any]], limit: int = 8) -> str:
    if not history_messages:
        return "（无历史上下文）"
    selected = history_messages[-max(int(limit), 1):]
    lines: List[str] = []
    for item in selected:
        role = str(item.get("role", "user")).lower()
        if role not in {"user", "assistant", "system"}:
            continue
        label = "用户" if role == "user" else "助手" if role == "assistant" else "系统"
        content = _normalize_content(item.get("content", ""))
        if content:
            lines.append(f"{label}: {content}")
    return "\n".join(lines) if lines else "（无可用历史）"


def build_codex_exec_prompt(
    raw_user_input: str,
    handoff_summary: str,
    task: str,
    context: str,
    history_messages: List[Dict[str, Any]],
    history_limit: int = 8,
) -> str:
    core_task = (task or raw_user_input or "").strip()
    summary = (handoff_summary or "已完成意图识别，转入 Codex 执行复杂任务。").strip()
    context_block = (context or "（无）").strip()
    history_block = build_history_excerpt(history_messages, limit=history_limit)
    return (
        f"{CODEX_EXEC_PROMPT}\n\n"
        "## 用户原话\n"
        f"{(raw_user_input or '').strip()}\n\n"
        "## 上游阶段总结\n"
        f"{summary}\n\n"
        "## 本次执行目标\n"
        f"{core_task}\n\n"
        "## 额外上下文\n"
        f"{context_block}\n\n"
        "## 最近对话上下文\n"
        f"{history_block}"
    )
