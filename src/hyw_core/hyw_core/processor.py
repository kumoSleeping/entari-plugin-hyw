from dataclasses import dataclass, field
from typing import List, Callable, Awaitable

@dataclass
class ProcessedResponse:
    text: str
    images: List[str] = field(default_factory=list)

# 处理器函数签名
ResponseProcessor = Callable[[str], Awaitable[ProcessedResponse]]

async def apply_processors(content: str, processors: List[ResponseProcessor]) -> ProcessedResponse:
    """依次应用所有处理器"""
    current_text = content
    all_images = []

    for proc in processors:
        result = await proc(current_text)
        current_text = result.text
        all_images.extend(result.images)

    return ProcessedResponse(text=current_text, images=all_images)
