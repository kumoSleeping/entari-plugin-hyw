import tempfile
import base64
import os
import re
from typing import List, Dict, Any, Optional
from .browser.renderer import get_content_renderer
from .models import ToolResult


def renumber_references_and_content(content: str, references: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    """
    根据正文中引用的顺序，对 references 进行筛选和重编号，并更新正文中的引用标记。
    例如：正文引用了 [12] 和 [34]，则重编号为 [1] 和 [2]，并只保留这两个 reference。
    如果正文没有引用任何内容，则返回原始列表（为了防止信息丢失）。
    """
    if not content or not references:
        return content, references or []

    # 1. 建立 reference 索引映射 (index -> ref)
    ref_map = {}
    for ref in references:
        # 优先使用 index 字段，如果没有则尝试 original_idx
        idx = ref.get("index") or ref.get("original_idx")
        if idx is not None:
            try:
                ref_map[int(idx)] = ref
            except (ValueError, TypeError):
                continue

    if not ref_map:
        return content, references

    # 2. 提取正文中所有引用的序号 [N]
    matches = list(re.finditer(r'\[(\d+)\]', content))

    if not matches:
        # 如果正文没有引用，直接返回原列表
        return content, references

    # 提取所有出现的序号，保持顺序，去重
    cited_indices = []
    seen = set()
    for m in matches:
        try:
            idx = int(m.group(1))
            # 只保留存在的引用
            if idx in ref_map and idx not in seen:
                seen.add(idx)
                cited_indices.append(idx)
        except ValueError:
            continue

    if not cited_indices:
        return content, references

    # 3. 建立旧序号 -> 新序号的映射
    # 例如: cited_indices=[12, 34], mapping={12: 1, 34: 2}
    old_to_new = {old_idx: i + 1 for i, old_idx in enumerate(cited_indices)}

    # 4. 替换正文中的引用
    def replace_func(match):
        try:
            old_idx = int(match.group(1))
            if old_idx in old_to_new:
                return f"[{old_to_new[old_idx]}]"
            # 如果引用的序号不在筛选列表中（可能是无效引用），保持原样或者去掉？
            # 保持原样比较安全
            return match.group(0)
        except ValueError:
            return match.group(0)

    new_content = re.sub(r'\[(\d+)\]', replace_func, content)

    # 5. 构建新的 references 列表
    new_references = []
    for old_idx in cited_indices:
        original_ref = ref_map[old_idx]
        # 创建副本以免修改原始数据
        new_ref = original_ref.copy()
        new_ref['index'] = old_to_new[old_idx] # 更新为新序号
        new_ref['original_idx'] = old_idx      # 保留原始序号记录
        new_references.append(new_ref)

    return new_content, new_references


def reorder_references_by_scoring(
    references: List[Dict[str, Any]],
    scoring: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """根据评分重排序 references，高分在前"""
    if not scoring or not references:
        return references

    # 构建 index -> score 映射
    score_map = {item["index"]: item["score"] for item in scoring}

    # 为每个 reference 添加 score（默认 0）
    for i, ref in enumerate(references):
        ref_index = ref.get("original_idx", i + 1)
        ref["_score"] = score_map.get(ref_index, 0)

    # 按分数降序排序
    sorted_refs = sorted(references, key=lambda x: x.get("_score", 0), reverse=True)

    # 清理临时字段
    for ref in sorted_refs:
        ref.pop("_score", None)

    return sorted_refs


async def render(
    content: str,
    title: str = "Assistant Response",
    headless: bool = True,
    theme_color: str = "#ef4444",
    references: Optional[List[Dict[str, Any]]] = None,
    scoring: Optional[List[Dict[str, Any]]] = None,
    prepared_tab_id: Optional[str] = None,
) -> ToolResult:
    """渲染内容为图片卡片

    Args:
        content: Markdown 内容
        title: 标题
        headless: 是否无头模式
        theme_color: 主题颜色
        references: 搜索结果来源列表
        scoring: 评分信息，用于重排序 references
    """
    print(f"  [RenderTool] Rendering card: {title}")

    try:
        renderer = await get_content_renderer(headless=headless)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name

        tab_id = prepared_tab_id or await renderer.prepare_tab()
        close_tab_after_render = prepared_tab_id is None

        # 根据评分重排序 references - 已移除，防止序号错乱
        # sorted_references = reorder_references_by_scoring(references or [], scoring)
        # sorted_references = references or []

        # 智能重编号：根据正文引用顺序筛选并重排
        final_content, sorted_references = renumber_references_and_content(content, references or [])

        success = await renderer.render(
            markdown_content=final_content,
            output_path=output_path,
            tab_id=tab_id,
            close_tab=close_tab_after_render,
            theme_color=theme_color,
            references=sorted_references
        )

        if success and os.path.exists(output_path):
            with open(output_path, "rb") as f:
                img_data = f.read()
                b64 = base64.b64encode(img_data).decode()

            os.remove(output_path)

            return ToolResult(content=f"[RENDER_SUCCESS] Card created.\n[IMAGE_BASE64: {b64}]", should_finish=True)
        else:
            return ToolResult(content="Error: Render failed internally (no output file).", should_finish=False)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return ToolResult(content=f"Error during rendering: {str(e)}", should_finish=False)
