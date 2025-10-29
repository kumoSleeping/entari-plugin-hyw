from dataclasses import dataclass
import html
from typing import List, Text, Union
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
from satori.element import Custom, E

# 导入AI服务模块
from .agent import  AgentService, HywConfig


metadata(
    name="hyw",
    author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}],
    version="0.1.0",
    description="",
    config=HywConfig,
)

conf = plugin_config(HywConfig)
agent_service = AgentService(conf)
command_name_list = [conf.hyw_command_name] if isinstance(conf.hyw_command_name, str) else conf.hyw_command_name
alc = Alconna(command_name_list, Args["all_param;?", AllParam], meta=CommandMeta(compact=True,))


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
            message_chain.extend(session.reply.origin.message)
        except Exception:
            pass
    message_chain = message_chain.get(Text) + message_chain.get(Image) + message_chain.get(Custom)
    res = alc.parse(message_chain)
    if not res.matched:
        # logger.info(res.error_info)
        return

    mc = MessageChain(res.all_param) # type: ignore
    try:
        # 获取文本和图片
        msg = mc.get(Text).strip() if mc.get(Text) else ""
        logger.info(msg)
        
        # 检查是否有 onebot:json 元素（QQ小程序分享）
        if mc.get(Custom):
            custom_elements = [e for e in mc if isinstance(e, Custom)]
            
            # 删除 当前QQ版本不支持此应用，请升级 这句话
            for i in msg:
                i = str(i).replace("当前QQ版本不支持此应用，请升级", "")
                logger.info("删除不支持应用提示")
                
            for custom in custom_elements:
                if custom.tag == 'onebot:json':
                    # await react("📫")
                    decoded_json = process_onebot_json(custom.attributes())
                    msg += decoded_json
                    # 这里假设都是小程序分享，直接返回第一个(只有可能是一个)
                    break
        images = None
        if mc.get(Image):
            urls = mc[Image].map(lambda x: x.src)
            tasks = [download_image(url) for url in urls]
            images = await asyncio.gather(*tasks)
            await react("🍧")
        await react("✨")
        # 调用AI服务
        res_agent = await agent_service.unified_completion(str(msg), images)
        # await react("🐳")
        # 发送回复
        response_content = str(res_agent.content) if hasattr(res_agent, 'content') else ""
        if not response_content.strip():
            response_content = "[KEY] :: 信息处理 | 内容获取\n>> [search enable]\n抱歉，获取到的内容可能包含敏感信息，暂时无法显示完整结果。\n[LLM] :: 安全过滤"
        await session.send([Quote(session.event.message.id), response_content])
    except Exception as e:
        await react("❌")
        raise e

    