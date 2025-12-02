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

# async def process_images(message_chain: MessageChain) -> tuple[List[str], Optional[str]]:
#     """Process images from message chain"""
#     images = []
#     for elem in message_chain.get(Image):
#         if elem.src:
#             img_url = str(elem.src)
#             if img_url.startswith("http"):
#                 try:
#                     async with httpx.AsyncClient() as client:
#                         resp = await client.get(img_url, timeout=30)
#                         if resp.status_code == 200:
#                             mime_type = resp.headers.get("content-type", "image/png")
#                             b64_str = base64.b64encode(resp.content).decode('utf-8')
#                             images.append(f"data:{mime_type};base64,{b64_str}")
#                         else:
#                             logger.warning(f"Failed to download image: {img_url}, status: {resp.status_code}")
#                 except Exception as e:
#                     logger.warning(f"Error downloading image {img_url}: {e}")
#             else:
#                 images.append(img_url)
#     return images, None



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


def normalize_name(name: str) -> str:
    """Normalize name for fuzzy matching: remove '-' and replace '/' with space"""
    return name.lower().replace("/", " ").replace("-", "")

def resolve_model_name(input_str: str, models_config: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve model name from keyword matching.
    Returns: (resolved_model_name, error_message)
    """
    if not input_str:
        return None, None

    # 1. Exact match
    for m in models_config:
        if m.get("name") == input_str:
            return input_str, None

    # 2. Fuzzy matching with normalization
    # Rule: 
    # - Replace '/' with space (treat as two words)
    # - Remove '-' (connect as one word)
    norm_input = normalize_name(input_str)
    keywords = norm_input.split()
    matches = []
    
    for m in models_config:
        name = m.get("name", "")
        norm_name = normalize_name(name)
        
        if all(kw in norm_name for kw in keywords):
            matches.append(name)
            
    if len(matches) == 1:
        return matches[0], None
    elif len(matches) > 1:
        # Try to find exact normalized match among candidates
        exact_norm = [m for m in matches if normalize_name(m) == norm_input]
        if len(exact_norm) == 1:
            return exact_norm[0], None
            
        return None, f"找到多个匹配的模型: {', '.join(matches[:5])}{'...' if len(matches)>5 else ''}"
    
    return None, f"未找到包含 '{input_str}' 的模型"
