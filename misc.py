import json
import base64
import httpx
import re
import time
import io
from typing import Dict, Any, List, Optional
from loguru import logger
from arclet.entari import MessageChain, Image
from typing import Tuple
import asyncio
from satori.exception import ActionFailed
from PIL import Image as PILImage


def compress_image_b64(b64_data: str, quality: int = 85) -> str:
    """使用 PIL 压缩 base64 图片质量，不改变尺寸"""
    img_bytes = base64.b64decode(b64_data)
    img = PILImage.open(io.BytesIO(img_bytes))

    # 转换为 RGB（去除 alpha 通道，JPEG 不支持）
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    # 压缩输出
    output = io.BytesIO()
    img.save(output, format='JPEG', quality=quality, optimize=True)
    return base64.b64encode(output.getvalue()).decode()

def process_onebot_json(data: Dict[str, Any]) -> str:
    """Process OneBot JSON elements"""
    try:
        if "data" in data:
            json_str = data["data"]
            if isinstance(json_str, str):
                json_str = json_str.replace("&quot;", '"').replace("&#44;", ",")
                content = json.loads(json_str)
                if "meta" in content and "detail_1" in content["meta"]:
                    detail = content["meta"]["detail_1"]
                    if "desc" in detail and "qqdocurl" in detail:
                        return f"[Shared Document] {detail['desc']}: {detail['qqdocurl']}"
    except Exception as e:
        logger.warning(f"Failed to process JSON element: {e}")
    return ""


async def download_image(url: str) -> bytes:
    """下载图片"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
            else:
                raise ActionFailed(f"下载图片失败，状态码: {resp.status_code}")
    except Exception as e:
        raise ActionFailed(f"下载图片失败: {url}, 错误: {str(e)}")

async def process_images(mc: MessageChain, vision_model: Optional[str] = None) -> Tuple[List[str], Optional[str]]:
    # If vision model is explicitly set to "off", skip image processing
    if vision_model == "off":
        return [], None

    has_images = bool(mc.get(Image))
    images = []
    if has_images:
        urls = mc[Image].map(lambda x: x.src)
        tasks = [download_image(url) for url in urls]
        raw_images = await asyncio.gather(*tasks)
        # 先转 base64，再用 PIL 压缩（只压缩质量，不改变尺寸）
        for img in raw_images:
            b64_raw = base64.b64encode(img).decode('utf-8')
            b64_compressed = compress_image_b64(b64_raw, quality=85)
            images.append(b64_compressed)

    return images, None


def resolve_model_name(name: str, models_config: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve a user input model name to the full API model name from config.
    Supports partial matching if unique.
    """
    if not name:
        return None, "No model name provided"
        
    name = name.lower()
    
    # 1. Exact match (name or id or shortname)
    for m in models_config:
        if m.get("name") == name or m.get("id") == name:
            return m.get("name"), None
            
    # 2. Key/Shortcut match
    # Assuming the config might have keys like 'gpt4' mapping to full name
    # But usually models list is [{'name': '...', 'provider': '...'}, ...]
    
    # Check if 'name' matches any model 'name' partially?
    # Or just return the name itself if it looks like a valid model ID (contains / or -)
    if "/" in name or "-" in name or "." in name:
        return name, None
        
    # If not found in config specific list, and doesn't look like an ID, maybe return error
    # But let's look for partial match in config names
    matches = [m["name"] for m in models_config if name in m.get("name", "").lower()]
    if len(matches) == 1:
        return matches[0], None
    elif len(matches) > 1:
        return None, f"Model name '{name}' is ambiguous. Matches: {', '.join(matches[:3])}..."
        
    # Default: assume it's a valid ID passed directly
    return name, None


# Hardcoded markdown for refuse answer
REFUSE_ANSWER_MARKDOWN = """
<summary>
Instruct 专家分配此任务流程失败，请尝试提出其他问题~
</summary>
"""


async def render_refuse_answer(
    renderer,
    output_path: str,
    reason: str = "Instruct 专家分配此任务流程失败，请尝试提出其他问题~",
    theme_color: str = "#ef4444",
    tab_id: str = None,
) -> bool:
    """
    Render a refuse-to-answer image using the provided reason.
    
    Args:
        renderer: ContentRenderer instance
        output_path: Path to save the output image
        reason: The refusal reason to display
        theme_color: Theme color for the card
        tab_id: Optional tab ID for reusing a prepared browser tab
        
    Returns:
        True if render succeeded, False otherwise
    """
    markdown = f"""
# 任务中止

> {reason}
"""
    return await renderer.render(
        markdown_content=markdown,
        output_path=output_path,
        stats={},
        references=[],
        page_references=[],
        image_references=[],
        stages_used=[],
        image_timeout=1000,
        theme_color=theme_color,
        tab_id=tab_id,
    )


IMAGE_UNSUPPORTED_MARKDOWN = """
<summary>
当前模型不支持图片输入，请使用支持视觉能力的模型或仅发送文本。
</summary>
"""

async def render_image_unsupported(
    renderer,
    output_path: str,
    theme_color: str = "#ef4444",
    tab_id: str = None
) -> bool:
    """
    Render a card indicating that the model does not support image input.
    """
    markdown = f"""
# 图片输入不支持

> 当前选择的模型不支持图片输入。
> 请切换到支持视觉的模型，或仅发送文本内容。
"""
    return await renderer.render(
        markdown_content=markdown,
        output_path=output_path,
        stats={},
        references=[],
        page_references=[],
        image_references=[],
        stages_used=[],
        image_timeout=1000,
        theme_color=theme_color,
        tab_id=tab_id
    )


def parse_color(color: str) -> str:
    """Parse color string to hex format."""
    if not color:
        return "#ef4444"
    color = str(color).strip()
    if color.startswith('#') and len(color) in [4, 7]:
        return color
    if re.match(r'^[0-9a-fA-F]{6}$', color):
        return f'#{color}'
    rgb_match = re.match(r'^\(?(\d+)[,\s]+(\d+)[,\s]+(\d+)\)?$', color)
    if rgb_match:
        r, g, b = (max(0, min(255, int(x))) for x in rgb_match.groups())
        return f'#{r:02x}{g:02x}{b:02x}'
    return "#ef4444"


class RecentEventDeduper:
    """Deduplicates recent events based on a key with TTL."""
    
    def __init__(self, ttl_seconds: float = 30.0, max_size: int = 2048):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._seen: Dict[str, float] = {}

    def seen_recently(self, key: str) -> bool:
        now = time.time()
        if len(self._seen) > self.max_size:
            self._prune(now)
        ts = self._seen.get(key)
        if ts is None or now - ts > self.ttl_seconds:
            self._seen[key] = now
            return False
        return True

    def _prune(self, now: float):
        expired = [k for k, ts in self._seen.items() if now - ts > self.ttl_seconds]
        for k in expired:
            self._seen.pop(k, None)
