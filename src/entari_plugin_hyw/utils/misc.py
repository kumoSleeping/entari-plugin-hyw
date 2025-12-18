import json
import base64
import httpx
from typing import Dict, Any, List, Optional
from loguru import logger
from arclet.entari import MessageChain, Image
from typing import Tuple
import asyncio
from satori.exception import ActionFailed

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
        import base64
        images = [base64.b64encode(img).decode('utf-8') for img in raw_images]
    
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
