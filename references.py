import json
from typing import Any, Dict, List


_MAX_LOG_CONTENT_CHARS = 1800
_MAX_LOG_VISIBLE_CHARS = 600
_MAX_LOG_ELEMENTS = 8


def extract_references_from_messages(api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    references: List[Dict[str, Any]] = []
    index = 1

    for msg in api_messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            content = " ".join(text_parts)
        if msg.get("role") != "user" or not isinstance(content, str) or not content.startswith("[Tool Result:"):
            continue

        try:
            json_start = content.find("{")
            if json_start == -1:
                continue
            parsed = json.loads(content[json_start:])
            results = parsed.get("results", [])
            for res in results:
                references.append(
                    {
                        "title": res.get("title", f"Result {index}"),
                        "url": res.get("url", ""),
                        "snippet": res.get("snippet", "") or res.get("content", ""),
                        "original_idx": index,
                    }
                )
                index += 1
        except (json.JSONDecodeError, TypeError):
            continue

    return references


def _compact_tool_result(content: str) -> str:
    if not content.startswith("[Tool Result:"):
        return content

    json_start = content.find("{")
    if json_start == -1:
        return content

    header = content[:json_start].rstrip()
    try:
        parsed = json.loads(content[json_start:])
    except (json.JSONDecodeError, TypeError):
        return content[:_MAX_LOG_CONTENT_CHARS] + ("\n...[truncated]" if len(content) > _MAX_LOG_CONTENT_CHARS else "")

    if header.startswith("[Tool Result: browser_action]"):
        keep: Dict[str, Any] = {}
        has_search_results = bool(parsed.get("search_results"))
        has_page_content = bool(parsed.get("content"))
        has_notice = bool(parsed.get("page_notice"))

        for key in ("ok", "action", "active_tab_id", "tabs", "title", "url", "error", "detail", "page_notice", "observe_warnings"):
            if key in parsed:
                keep[key] = parsed[key]

        if "page_state" in parsed:
            state = parsed.get("page_state") or {}
            if isinstance(state, dict):
                keep["page_state"] = {
                    key: state.get(key)
                    for key in ("at_bottom", "remaining_y")
                    if key in state
                }
            else:
                keep["page_state"] = state

        if "auto_scan" in parsed:
            scan = parsed.get("auto_scan") or {}
            final_state = scan.get("final_state") if isinstance(scan, dict) else {}
            keep["auto_scan"] = {
                "enabled": scan.get("enabled", True) if isinstance(scan, dict) else True,
                "at_bottom": bool(final_state.get("at_bottom")) if isinstance(final_state, dict) else bool(scan.get("at_bottom")) if isinstance(scan, dict) else False,
            }

        if has_search_results:
            compact_results = []
            for result in parsed.get("search_results", [])[:6]:
                if isinstance(result, dict):
                    compact_results.append({
                        key: (str(result.get(key, ""))[:300] if key == "snippet" else result.get(key, ""))
                        for key in ("title", "url", "snippet", "ref")
                        if result.get(key, "") != ""
                    })
            keep["search_results"] = compact_results

        visible_text = parsed.get("visible_text")
        if isinstance(visible_text, str) and visible_text and (has_notice or (not has_search_results and not has_page_content)):
            keep["visible_text_excerpt"] = visible_text[:_MAX_LOG_VISIBLE_CHARS]
            if len(visible_text) > _MAX_LOG_VISIBLE_CHARS:
                keep["visible_text_truncated"] = True

        page_content = parsed.get("content")
        if isinstance(page_content, str) and page_content and not has_search_results:
            keep["content_excerpt"] = page_content[:_MAX_LOG_CONTENT_CHARS]
            if len(page_content) > _MAX_LOG_CONTENT_CHARS:
                keep["content_truncated"] = True

        elements = parsed.get("interactive_elements")
        if isinstance(elements, list) and elements and (has_notice or (not has_search_results and not has_page_content)):
            compact_elements = []
            for element in elements[:_MAX_LOG_ELEMENTS]:
                if isinstance(element, dict):
                    compact_elements.append({
                        key: element.get(key, "")
                        for key in ("ref", "tag", "role", "text", "selector")
                        if element.get(key, "") != ""
                    })
            keep["interactive_elements_excerpt"] = compact_elements
            if len(elements) > _MAX_LOG_ELEMENTS:
                keep["interactive_elements_truncated"] = len(elements) - _MAX_LOG_ELEMENTS

        return f"{header}\n{json.dumps(keep, ensure_ascii=False, indent=2)}"

    return content


def format_conversation_log(api_messages: List[Dict[str, Any]]) -> str:
    blocks = []
    system_seen = False
    for msg in api_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        parts.append("[IMAGE]")
                else:
                    parts.append(str(part))
            content = " ".join(parts)

        if role == "user":
            tag = "user"
        elif role == "system":
            tag = "sys_temp" if msg.get("_temp") else "sys"
        elif role == "assistant":
            tag = "llm"
        else:
            tag = role

        if role == "system" and not msg.get("_temp"):
            if system_seen:
                continue
            system_seen = True
            content = "[system prompt omitted from log]"
        elif role == "user" and isinstance(content, str):
            content = _compact_tool_result(content)

        blocks.append(f"```{tag}\n{content}\n```")

    return "\n\n".join(blocks)
