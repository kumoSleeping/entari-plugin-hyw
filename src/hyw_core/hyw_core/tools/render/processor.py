from ...processor import ProcessedResponse

async def render_processor(content: str) -> ProcessedResponse:
    text = content
    images = []

    # 循环提取所有 [IMAGE_BASE64: ...] 标记
    marker = "[IMAGE_BASE64:"
    while marker in text:
        start = text.find(marker)
        end = text.find("]", start)
        if end == -1:
            break

        b64 = text[start + len(marker):end].strip()
        if b64:
            images.append(b64)

        # 从文本中移除该标记
        text = text[:start] + text[end+1:]

    # 如果有图片，清空文本（文本已渲染成图片）
    if images:
        text = ""

    return ProcessedResponse(text=text.strip(), images=images)
