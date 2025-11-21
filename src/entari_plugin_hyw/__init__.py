from dataclasses import dataclass
import html
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Text, Tuple, Union, TYPE_CHECKING, cast
from typing_extensions import override
from arclet.entari import metadata
from arclet.entari import MessageChain, Session
from arclet.entari.event.base import MessageEvent
from loguru import logger
from satori.exception import ActionFailed
from arclet.entari import MessageChain, Image, Quote, Text
import arclet.letoderea as leto
from arclet.entari import MessageCreatedEvent, Session
from arclet.entari import BasicConfModel, metadata, plugin_config
import httpx
import asyncio
import json
import re
from arclet.alconna import (
    Args,
    Alconna,
    AllParam,
    MultiVar,
    CommandMeta,
    Option,
)
from arclet.entari import MessageChain, Session, command
from arclet.entari import plugin, Ready, Cleanup, Startup
from satori.element import Custom, E
from .hyw_core import HYW, HYWConfig

# 全局变量
hyw_core = None

class HistoryManager:
    def __init__(self, max_records: int = 20):
        self.max_records = max_records
        self._order: Deque[str] = deque()
        self._store: Dict[str, List[dict]] = {}
        self._bindings: Dict[str, Set[str]] = {}
        self._msg_map: Dict[str, str] = {}

    def extract_message_id(self, message_like: Any) -> Optional[str]:
        if message_like is None:
            return None
        if isinstance(message_like, (list, tuple)):
            for item in message_like:
                mid = self.extract_message_id(item)
                if mid:
                    return mid
            return None
        if isinstance(message_like, dict):
            for key in ("message_id", "id"):
                value = message_like.get(key)
                if value:
                    return str(value)
        for attr in ("message_id", "id"):
            value = getattr(message_like, attr, None)
            if value:
                return str(value)
        nested = getattr(message_like, "message", None)
        if nested is not None and nested is not message_like:
            return self.extract_message_id(nested)
        return None

    def remove(self, conversation_id: Optional[str], *, remove_from_order: bool = True) -> None:
        if not conversation_id:
            return
        cid = str(conversation_id)
        if remove_from_order:
            try:
                self._order.remove(cid)
            except ValueError:
                pass
        bindings = self._bindings.pop(cid, set())
        for msg_id in bindings:
            self._msg_map.pop(msg_id, None)
        self._store.pop(cid, None)

    def _enforce_limit(self) -> None:
        while len(self._order) > self.max_records:
            obsolete = self._order.popleft()
            self.remove(obsolete, remove_from_order=False)

    def remember(self, conversation_id: Optional[str], history: Optional[List[dict]], related_ids: List[Optional[str]]) -> None:
        if not conversation_id or not history:
            return
        cid = str(conversation_id)
        self._store[cid] = list(history)
        binding_ids = {str(mid) for mid in related_ids if mid}
        self._bindings[cid] = binding_ids
        for mid in binding_ids:
            self._msg_map[mid] = cid
        self._order.append(cid)
        self._enforce_limit()

    def get_history(self, msg_id: str) -> Optional[List[dict]]:
        cid = self._msg_map.get(msg_id)
        if cid:
            return list(self._store.get(cid, []))
        return None
    
    def get_conversation_id(self, msg_id: str) -> Optional[str]:
        return self._msg_map.get(msg_id)

history_manager = HistoryManager()

# Request lock for HYW agent
_hyw_request_lock: Optional[asyncio.Lock] = None

def _get_hyw_request_lock() -> asyncio.Lock:
    global _hyw_request_lock
    if _hyw_request_lock is None:
        _hyw_request_lock = asyncio.Lock()
    return _hyw_request_lock


class HywConfig(BasicConfModel):
    command_name_list: Union[str, List[str]] = "hyw"
    model_name: str
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    headless: bool = False
    save_conversation: bool = False
    
    browser_tool: str = "jina"
    jina_api_key: Optional[str] = None
    
    vision_model_name: Optional[str] = None
    vision_base_url: Optional[str] = None
    vision_api_key: Optional[str] = None
    # verbose: bool = False

metadata(
    "hyw",
    author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}],
    version="0.1.0",
    description="",
    config=HywConfig,
)

conf = plugin_config(HywConfig)
alc = Alconna(
    conf.command_name_list,
    Option("-t|--text", dest="text_only", default=False, help_text="仅文本模式(禁用图片识别)"),
    Args["all_param", AllParam],
    # Option("-v|--verbose", dest="verbose", default=False, help_text="启用详细日志输出"),
    meta=CommandMeta(compact=False)
)

# Create HYW configuration
hyw_config = HYWConfig(
    api_key=conf.api_key,
    model_name=conf.model_name,
    base_url=conf.base_url,
    save_conversation=conf.save_conversation,
    headless=conf.headless,
    browser_tool=conf.browser_tool,
    jina_api_key=conf.jina_api_key,
    vision_model_name=conf.vision_model_name,
    vision_base_url=conf.vision_base_url,
    vision_api_key=conf.vision_api_key
)

hyw = HYW(config=hyw_config)



# Emoji到代码的映射字典
EMOJI_TO_CODE = {
    "🐳": "128051",
    "❌": "10060",
    "🍧": "127847",
    "✨": "10024",
    "📫": "128235"
}

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


def process_onebot_json(json_data_str: str) -> str:
    try:
        # 解码HTML实体
        json_str = html.unescape(json_data_str)
        return json_str
    except Exception as e:
        return json_data_str


async def react(session: Session, emoji: str):
    try:
        if session.event.login.platform == "onebot":
            code = EMOJI_TO_CODE.get(emoji, "10024")
            await session.account.protocol.call_api("internal/set_group_reaction", {"group_id": int(session.guild.id), "message_id": int(session.event.message.id), "code": code, "is_add": True})
        else:
            await session.reaction_create(emoji=emoji)
    except ActionFailed:
        pass

def handle_shortcut(message_chain: MessageChain) -> Tuple[bool, str]:
    current_msg_text = str(message_chain.get(Text)) if message_chain.get(Text) else ""
    is_shortcut = False
    shortcut_replacement = ""
    if current_msg_text.strip().startswith("/"):
        is_shortcut = True
        shortcut_replacement = current_msg_text.strip()[1:]
    return is_shortcut, shortcut_replacement

async def process_images(mc: MessageChain, parse_result: Any) -> Tuple[List[str], Optional[str]]:
    is_text_only = False
    if parse_result.matched:
        def get_bool_value(val):
            if hasattr(val, 'value'):
                return bool(val.value)
            return bool(val)
        is_text_only = get_bool_value(getattr(parse_result, 'text_only', False))
    
    text_str = str(mc.get(Text) or "")
    if not is_text_only and re.search(r'(?:^|\s)(-t|--text)(?:$|\s)', text_str):
        is_text_only = True
    
    if is_text_only:
        logger.info("检测到仅文本模式参数，跳过图片分析")
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

@leto.on(MessageCreatedEvent)
async def on_message_created(message_chain: MessageChain, session: Session[MessageEvent]):
    # Skip if no substantial content in original message
    original_text = str(message_chain.get(Text)).strip()
    has_images = bool(message_chain.get(Image))
    has_custom = bool(message_chain.get(Custom))
    if not original_text and not has_images and not has_custom:
        return
    
    if session.reply:
        try:
            message_chain.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception:
            pass
            
    message_chain = message_chain.get(Text) + message_chain.get(Image) + message_chain.get(Custom)
    
    quoted_message_id: Optional[str] = None
    conversation_history_key: Optional[str] = None
    conversation_history_payload: List[dict] = []
    
    if session.reply:
        try:
            quoted_message_id = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
        except Exception as e:
            logger.warning(f"提取引用消息ID失败: {e}")
            quoted_message_id = None
            
        if quoted_message_id:
            conversation_history_key = history_manager.get_conversation_id(quoted_message_id)
            if conversation_history_key:
                conversation_history_payload = history_manager.get_history(quoted_message_id) or []
                logger.info(f"继续对话模式触发, 引用消息ID: {quoted_message_id}, 历史长度: {len(conversation_history_payload)}")

    parse_result = alc.parse(message_chain)
    is_shortcut, shortcut_replacement = handle_shortcut(message_chain)
    
    should_process = parse_result.matched or (bool(conversation_history_key) and is_shortcut)
    
    if not should_process:
        return

    raw_param_chain: MessageChain = parse_result.all_param if parse_result.matched else message_chain  # type: ignore
    if not parse_result.matched and is_shortcut:
        logger.debug(f"触发快捷指令，替换内容: {shortcut_replacement}")
        
    mc = MessageChain(raw_param_chain)
    
    async def process_request() -> None:
        await react(session, "✨")
        try:
            if is_shortcut and not parse_result.matched:
                msg = shortcut_replacement
            else:
                msg = mc.get(Text).strip() if mc.get(Text) else ""
            
            if mc.get(Custom): # type: ignore
                custom_elements = [e for e in mc if isinstance(e, Custom)]
                for custom in custom_elements:
                    if custom.tag == 'onebot:json':
                        decoded_json = process_onebot_json(custom.attributes())
                        msg += decoded_json
                        break
            
            time_start = time.perf_counter()
            images, error_msg = await process_images(mc, parse_result)
            
            if error_msg:
                await session.send(error_msg)
                return

            lock = _get_hyw_request_lock()
            async with lock:
                response = await hyw.agent(str(msg), conversation_history=conversation_history_payload, images=images)
            
            response_content = response.get("llm_response", "") if isinstance(response, dict) else ""
            new_history = response.get("conversation_history", []) if isinstance(response, dict) else []
            
            try:
                send_result = await session.send([Quote(session.event.message.id), response_content])
            except ActionFailed as e:
                if "9057" in str(e):
                    logger.warning(f"发送消息失败(9057)，尝试截断发送: {e}")
                    truncated_content = response_content[:1000] + "\n\n[...内容过长，已大幅截断...]"
                    send_result = await session.send([Quote(session.event.message.id), truncated_content])
                else:
                    raise e
            
            sent_message_id = history_manager.extract_message_id(send_result)
            current_user_message_id = str(session.event.message.id)
            related_ids: List[Optional[str]] = [current_user_message_id, sent_message_id]
            
            if conversation_history_key:
                history_manager.remove(conversation_history_key)
                related_ids.append(quoted_message_id)
            
            history_manager.remember(sent_message_id, new_history, related_ids)
            
        except Exception as exc:
            await react(session, "❌")
            logger.exception("处理HYW消息失败: {}", exc)

    asyncio.create_task(process_request())
    return


