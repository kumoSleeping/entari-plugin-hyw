from ...processor import ProcessedResponse

async def render_processor(content: str) -> ProcessedResponse:
    text = content
    images = []

    # 循环提取所有 [IMAGE_BASE64: ...] 标记
    marker = "[IMAGE_BASE64:"
    while marker in text:
        start = text.find(marker)
        end = text.find("]", start)

        # 强制检查修复：如果未闭合，默认渲染到文本结束
        # 这种行为是为了防止生成的图片数据过长导致截断时无法显示
        if end == -1:
            end = len(text)

        b64 = text[start + len(marker):end].strip()
        if b64:
            images.append(b64)

        # 从文本中移除该标记
        # 如果 end == len(text), text[end+1:] 为空字符串，符合预期
        text = text[:start] + text[end+1:]

    # 如果有图片，保留文本以便同时显示（取消之前的清空逻辑）
    # 默认行为就是渲染，如果不渲染（文本）需要告知，即保留文本
    # if images:
    #     text = ""

    return ProcessedResponse(text=text.strip(), images=images)
