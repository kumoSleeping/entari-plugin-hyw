import json
from typing import Any, Dict, List


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


def format_conversation_log(api_messages: List[Dict[str, Any]]) -> str:
    blocks = []
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

        blocks.append(f"```{tag}\n{content}\n```")

    return "\n\n".join(blocks)
