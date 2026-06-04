import re
from typing import Any, Dict, List, Optional


def extract_tag_content(content: str, tag: str) -> Optional[str]:
    match = re.search(
        rf"<{tag}[^>]*>(.*?)</{tag}>",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    text = match.group(1).strip()
    return text or None


def extract_tool_calls(content: str, available_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    tool_calls = []
    pattern = r'<tool_call\s+name=["\']?(\w+)["\']?\s*>(.*?)</tool_call>'
    matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)

    for name, params_xml in matches:
        if available_tools and name not in available_tools:
            continue
        tool_calls.append({"name": name, "arguments": parse_tool_params(params_xml)})

    return tool_calls


def parse_tool_params(params_xml: str) -> Dict[str, Any]:
    params = {}
    pattern = r"<(\w+)>(.*?)</\1>"
    matches = re.findall(pattern, params_xml, re.DOTALL)
    for key, value in matches:
        params[key] = value.strip()
    return params


def extract_scoring(content: str) -> Optional[List[Dict[str, Any]]]:
    scoring_content = extract_tag_content(content, "scoring")
    if not scoring_content:
        return None

    scoring = []
    item_pattern = r'<item\s+index=["\']?(\d+)["\']?\s+score=["\']?(\d+)["\']?\s*>(.*?)</item>'
    items = re.findall(item_pattern, scoring_content, re.DOTALL | re.IGNORECASE)

    for index, score, reason in items:
        scoring.append(
            {
                "index": int(index),
                "score": int(score),
                "reason": reason.strip(),
            }
        )

    return scoring or None


def extract_final_content(content: str) -> str:
    if not content:
        return ""

    without_scoring = re.sub(
        r"<scoring>.*?</scoring>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    execution_matches = re.findall(
        r"<execution_content[^>]*>(.*?)</execution_content>",
        without_scoring,
        flags=re.DOTALL | re.IGNORECASE,
    )
    for block in execution_matches:
        candidate = block.strip()
        if candidate:
            return remove_visual_separators(candidate)

    without_logic = re.sub(
        r"<response_logic[^>]*>.*?</response_logic>",
        "",
        without_scoring,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    if without_logic:
        return remove_visual_separators(without_logic)

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
        return remove_visual_separators(without_internal_tags)

    return remove_visual_separators(without_scoring)


def remove_visual_separators(content: str) -> str:
    """Remove standalone decorative separators from final Markdown."""
    if not content:
        return ""

    text = re.sub(r"(?im)^\s*<hr\s*/?>\s*$", "", content)
    text = re.sub(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,}|={3,}|-{2,}\s*-+|—{2,}|─{2,})\s*$", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_progress_hint(content: str) -> Optional[str]:
    hint = extract_tag_content(content, "progress_hint")
    if not hint:
        return None

    hint = re.sub(r"<[^>]+>", "", hint).strip()
    hint = hint.replace("\\n", "\n").strip()
    return hint or None
