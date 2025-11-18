from dataclasses import dataclass
import html
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Text, Union, TYPE_CHECKING, cast
from typing_extensions import override
from arclet.entari import metadata
from arclet.entari import MessageChain, Session
from arclet.entari.event.base import MessageEvent
from loguru import logger
from satori.exception import ActionFailed
from arclet.entari import MessageChain, At, Image, Quote, Text
from satori import Element
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
)
from arclet.entari import MessageChain, Session, command
from arclet.entari import plugin, Ready, Cleanup
from satori.element import Custom, E
from .hyw_core import HYW
# 全局变量
hyw_core = None
stack = None
sessions = None

MAX_HISTORY_RECORDS = 20
_history_order: Deque[str] = deque()
_history_store: Dict[str, List[dict]] = {}
_history_bindings: Dict[str, Set[str]] = {}
_message_to_history: Dict[str, str] = {}
_hyw_request_lock: Optional[asyncio.Lock] = None

def _get_hyw_request_lock() -> asyncio.Lock:
    global _hyw_request_lock
    if _hyw_request_lock is None:
        _hyw_request_lock = asyncio.Lock()
    return _hyw_request_lock


def _extract_message_id(message_like: Any) -> Optional[str]:
    if message_like is None:
        return None
    if isinstance(message_like, (list, tuple)):
        for item in message_like:
            mid = _extract_message_id(item)
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
        return _extract_message_id(nested)
    return None


def _remove_history_record(conversation_id: Optional[str], *, remove_from_order: bool = True) -> None:
    if not conversation_id:
        return
    cid = str(conversation_id)
    if remove_from_order:
        try:
            _history_order.remove(cid)
        except ValueError:
            pass
    bindings = _history_bindings.pop(cid, set())
    for msg_id in bindings:
        _message_to_history.pop(msg_id, None)
    _history_store.pop(cid, None)


def _enforce_history_limit() -> None:
    while len(_history_order) > MAX_HISTORY_RECORDS:
        obsolete = _history_order.popleft()
        _remove_history_record(obsolete, remove_from_order=False)


def _remember_history_record(conversation_id: Optional[str], history: Optional[List[dict]], related_ids: List[Optional[str]]) -> None:
    if not conversation_id or not history:
        return
    cid = str(conversation_id)
    history_copy = list(history)
    _history_store[cid] = history_copy
    binding_ids = {str(mid) for mid in related_ids if mid}
    _history_bindings[cid] = binding_ids
    for mid in binding_ids:
        _message_to_history[mid] = cid
    _history_order.append(cid)
    _enforce_history_limit()


class HywConfig(BasicConfModel):
    command_name_list: Union[str, List[str]] = "hyw"
    model_name: str
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    search_engine: str = "google"


metadata(
    "hyw",
    author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}],
    version="0.1.0",
    description="",
    config=HywConfig,
)

conf = plugin_config(HywConfig  )
alc = Alconna(conf.command_name_list, Args["all_param;?", AllParam], meta=CommandMeta(compact=True,))
hyw = HYW(
        api_key=conf.api_key,
        model_name=conf.model_name,
        base_url=conf.base_url,
        search_engine=conf.search_engine
    )

@plugin.listen(Ready)
async def on_ready():
    global hyw, hyw_core, stack, sessions
    stack, sessions = await hyw.connect_servers([
            {"name": "Playwright", "command": "npx", "args": ["-y", "@playwright/mcp@0.0.38", "--isolated", "--headless"],
             "env": {
                 "PLAYWRIGHT_VIEWPORT_WIDTH": "1920",
                 "PLAYWRIGHT_VIEWPORT_HEIGHT": "1080",
                 "PLAYWRIGHT_DEVICE_SCALE_FACTOR": "0.5",
                 "PLAYWRIGHT_HEADLESS": "false",
                 "HEADLESS": "false"
             }}
        ])
    hyw_core = hyw
    logger.success("Browser initialized!")
    
    
@plugin.listen(Cleanup)
async def on_cleanup():
    global stack
    print("Entari is ready!")
    logger.info("正在关闭浏览器...")
    if stack:
        await stack.aclose()
    logger.success("浏览器已关闭")

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


@leto.on(MessageCreatedEvent)
async def on_message_created(message_chain: MessageChain, session: Session[MessageEvent]):
    # 检查 hyw_core 是否已初始化
    global hyw_core
    if hyw_core is None:
        logger.warning("HYW not initialized, skipping message processing")
        return
        
    async def react(emoji: str):
        try:
            if session.event.login.platform == "onebot":
                code = EMOJI_TO_CODE.get(emoji, "10024")
                await session.account.protocol.call_api("internal/set_group_reaction", {"group_id": int(session.guild.id), "message_id": int(session.event.message.id), "code": code, "is_add": True})
            else:
                await session.reaction_create(emoji=emoji)
        except ActionFailed:
            pass

    if session.reply:
        try:
            message_chain.extend(session.reply.origin.message)
        except Exception:
            pass
    message_chain = message_chain.get(Text) + message_chain.get(Image) + message_chain.get(Custom)
    quoted_message_id: Optional[str] = None
    conversation_history_key: Optional[str] = None
    conversation_history_payload: List[dict] = []
    if session.reply:
        try:
            # session.reply.origin 是 Reply 对象，需要从它的 id 属性获取被引用的消息ID
            quoted_message_id = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
            logger.info(f"检测到引用消息, quoted_message_id: {quoted_message_id}")
        except Exception as e:
            logger.warning(f"提取引用消息ID失败: {e}")
            quoted_message_id = None
        if quoted_message_id:
            conversation_history_key = _message_to_history.get(quoted_message_id)
            if conversation_history_key:
                conversation_history_payload = list(_history_store.get(conversation_history_key, []))
                logger.info(f"继续对话模式触发, 引用消息ID: {quoted_message_id}, 历史长度: {len(conversation_history_payload)}")

    parse_result = alc.parse(message_chain)
    bypass_parse = bool(conversation_history_key)
    logger.info(f"bypass_parse: {bypass_parse}, quoted_message_id: {quoted_message_id}, conversation_history_key: {conversation_history_key}")
    if not parse_result.matched and not bypass_parse:
        # logger.info(parse_result.error_info)
        return
    raw_param_chain: MessageChain = parse_result.all_param if parse_result.matched else message_chain  # type: ignore
    if not parse_result.matched and bypass_parse:
        logger.debug("ALC未匹配但引用历史消息，直接放行")
    mc = MessageChain(raw_param_chain)
    
    async def process_request() -> None:
        await react("✨")
        logger.info(f"开始处理消息, bypass_parse: {bypass_parse}, msg: {mc.get(Text).strip() if mc.get(Text) else ''}")
        try:
            msg = mc.get(Text).strip() if mc.get(Text) else ""
            logger.info(msg)

            if mc.get(Custom): # type: ignore
                custom_elements = [e for e in mc if isinstance(e, Custom)]
                for i in msg:
                    i = str(i).replace("当前QQ版本不支持此应用，请升级", "")
                    logger.info("删除不支持应用提示")
                for custom in custom_elements:
                    if custom.tag == 'onebot:json':
                        decoded_json = process_onebot_json(custom.attributes())
                        msg += decoded_json
                        break

            image_base64 = None
            if mc.get(Image):
                urls = mc[Image].map(lambda x: x.src)
                tasks = [download_image(url) for url in urls]
                images = await asyncio.gather(*tasks)
                import base64
                image_base64 = base64.b64encode(images[0]).decode('utf-8')

            lock = _get_hyw_request_lock()
            async with lock:
                time_start = time.perf_counter()
                response = await hyw.agent(str(msg), image_base64=image_base64, conversation_history=conversation_history_payload)
            response_content = response.get("llm_response", "") if isinstance(response, dict) else ""
            total_time = time.perf_counter() - time_start
            if not response_content.strip():
                response_content = "[ERROE] \n>> 抱歉，获取到的内容可能包含敏感信息，暂时无法显示完整结果。"
            send_result = await session.send([Quote(session.event.message.id), response_content+f"\n\n[DEBUG:处理时间] :: {total_time:.2f} 秒"])
            new_history = response.get("conversation_history", []) if isinstance(response, dict) else []
            sent_message_id = _extract_message_id(send_result)
            current_user_message_id = str(session.event.message.id)
            related_ids: List[Optional[str]] = [current_user_message_id, sent_message_id]
            if conversation_history_key:
                _remove_history_record(conversation_history_key)
                related_ids.append(quoted_message_id)
            _remember_history_record(sent_message_id, new_history, related_ids)
        except Exception as exc:
            await react("❌")
            logger.exception("处理HYW消息失败: {}", exc)

    asyncio.create_task(process_request())
    return

