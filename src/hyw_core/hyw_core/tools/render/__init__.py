import tempfile
import base64
import os
from typing import List, Dict, Any, Optional
from .._public.browser.renderer import get_content_renderer
from ...models import ToolResult


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
    scoring: Optional[List[Dict[str, Any]]] = None
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

        tab_id = await renderer.prepare_tab()

        # 根据评分重排序 references
        sorted_references = reorder_references_by_scoring(references or [], scoring)

        success = await renderer.render(
            markdown_content=content,
            output_path=output_path,
            tab_id=tab_id,
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
