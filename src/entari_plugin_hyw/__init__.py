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
    Option,
)
from arclet.entari import MessageChain, Session, command
from arclet.entari import plugin, Ready, Cleanup, Startup
from satori.element import Custom, E
from .hyw_core import HYW
import builtins
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
    headless: bool = False
    debug: bool = False
    use_jina: bool = False
    vision_model_name: Optional[str] = None
    vision_base_url: Optional[str] = None
    vision_api_key: Optional[str] = None
    compress_content: bool = False
    compress_model_name: Optional[str] = None
    compress_base_url: Optional[str] = None
    # verbose: bool = False

metadata(
    "hyw",
    author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}],
    version="0.1.0",
    description="",
    config=HywConfig,
)

conf = plugin_config(HywConfig  )
alc = Alconna(
    conf.command_name_list,
    Option("-t|--text", dest="text_only", default=False, help_text="仅文本模式(禁用图片识别)"),
    Args["all_param", AllParam],
    # Option("-v|--verbose", dest="verbose", default=False, help_text="启用详细日志输出"),
    meta=CommandMeta(compact=False)
)
hyw = HYW(
        api_key=conf.api_key,
        model_name=conf.model_name,
        base_url=conf.base_url,
        debug=conf.debug,
        headless=conf.headless,
        use_jina=conf.use_jina,
        vision_model_name=conf.vision_model_name,
        vision_base_url=conf.vision_base_url,
        vision_api_key=conf.vision_api_key,
        compress_content=conf.compress_content,
        compress_model_name=conf.compress_model_name,
        compress_base_url=conf.compress_base_url
    )



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
            message_chain.extend(MessageChain(" ") + session.reply.origin.message)
            # message_chain.extend(session.reply.origin.message)
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
            # logger.info(f"检测到引用消息, quoted_message_id: {quoted_message_id}")
        except Exception as e:
            logger.warning(f"提取引用消息ID失败: {e}")
            quoted_message_id = None
        if quoted_message_id:
            conversation_history_key = _message_to_history.get(quoted_message_id)
            if conversation_history_key:
                conversation_history_payload = list(_history_store.get(conversation_history_key, []))
                logger.info(f"继续对话模式触发, 引用消息ID: {quoted_message_id}, 历史长度: {len(conversation_history_payload)}")
    # logger.info(f"收到消息: {message_chain}")
    parse_result = alc.parse(message_chain)
    
    # 快捷指令处理
    current_msg_text = str(message_chain.get(Text)) if message_chain.get(Text) else ""
    
    is_shortcut = False
    shortcut_replacement = ""
    
    # 检查是否以 / 开头
    if current_msg_text.strip().startswith("/"):
        is_shortcut = True
        # 去掉开头的 /
        shortcut_replacement = current_msg_text.strip()[1:]

    # 逻辑修改：
    # 1. 如果命令匹配 (parse_result.matched)，正常处理
    # 2. 如果不匹配，但存在引用回复 (conversation_history_key) 且 命中快捷指令 (is_shortcut)，允许处理
    # 3. 其他情况忽略
    
    should_process = parse_result.matched or (bool(conversation_history_key) and is_shortcut)
    
    # logger.info(f"should_process: {should_process}, matched: {parse_result.matched}, is_shortcut: {is_shortcut}, conversation_history_key: {conversation_history_key}")
    
    if not should_process:
        return

    # 检查是否使用了 -v/--verbose 参数
    # if parse_result.matched and hasattr(parse_result, 'verbose') and parse_result.verbose:
    #     logger.info("Verbose 模式已启用")
    
    raw_param_chain: MessageChain = parse_result.all_param if parse_result.matched else message_chain  # type: ignore
    
    if not parse_result.matched and is_shortcut:
        logger.debug(f"触发快捷指令，替换内容: {shortcut_replacement}")
        
    mc = MessageChain(raw_param_chain)
    
    async def process_request() -> None:
        await react("✨")
        # logger.info(f"开始处理消息, matched: {parse_result.matched}, is_shortcut: {is_shortcut}")
        
        try:
            # 如果是快捷指令，使用替换后的文本；否则获取原始文本
            if is_shortcut and not parse_result.matched:
                msg = shortcut_replacement
            else:
                msg = mc.get(Text).strip() if mc.get(Text) else ""
            
            # logger.info(msg)

            if mc.get(Custom): # type: ignore
                custom_elements = [e for e in mc if isinstance(e, Custom)]
                for custom in custom_elements:
                    if custom.tag == 'onebot:json':
                        decoded_json = process_onebot_json(custom.attributes())
                        msg += decoded_json
                        break
                    
            async def process_images_task():
                # Check flags
                is_text_only = False
                
                # Check from Alconna result
                if parse_result.matched:
                    # Handle Alconna result which might be an object
                    def get_bool_value(val):
                        if hasattr(val, 'value'):
                            return bool(val.value)
                        return bool(val)

                    is_text_only = get_bool_value(getattr(parse_result, 'text_only', False))
                
                # Manual check for shortcut or unmatched cases
                text_str = str(message_chain.get(Text) or "")
                if not is_text_only and re.search(r'(?:^|\s)(-t|--text)(?:$|\s)', text_str):
                    is_text_only = True
                
                # 2. Handle text only
                if is_text_only:
                    logger.info("检测到仅文本模式参数，跳过图片分析")
                    return [], None

                # 3. Check images
                has_images = bool(mc.get(Image))
                
                images = []
                if has_images:
                    urls = mc[Image].map(lambda x: x.src)
                    tasks = [download_image(url) for url in urls]
                    raw_images = await asyncio.gather(*tasks)
                    import base64
                    images = [base64.b64encode(img).decode('utf-8') for img in raw_images]
                
                return images, None
            
            time_start = time.perf_counter()
            
            # 并行启动浏览器和处理图片
            # browser_task = asyncio.create_task(start_browser_task())
            images_task = asyncio.create_task(process_images_task())
            
            # 等待两个任务完成
            # browser_result, images = await asyncio.gather(browser_task, images_task)
            # playwright, browser, context, page = browser_result
            images, error_msg = await images_task
            
            if error_msg:
                await session.send(error_msg)
                return

            lock = _get_hyw_request_lock()
            async with lock:
                # Run Agent
                # 传入 conversation_history_payload 以便 todo_list 使用上下文
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
        finally:
            # 确保浏览器被关闭
            # if playwright or browser or context:
            #     await hyw.close_browser(playwright, browser, context)
            pass

    asyncio.create_task(process_request())
    return


