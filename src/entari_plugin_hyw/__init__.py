from typing import List, Text, Union
from arclet.entari import metadata
from arclet.entari import MessageChain, Session
from arclet.entari.event.base import MessageEvent
from satori.exception import ActionFailed
from arclet.entari import MessageChain, At, Image, Quote, Text
import arclet.letoderea as leto
from arclet.entari import MessageCreatedEvent, Session
from arclet.entari import BasicConfModel, metadata, plugin_config
from loguru import logger
import asyncio
from arclet.alconna import (
    Args,
    Alconna,
    AllParam,
    MultiVar,
    CommandMeta,
)
from arclet.entari import MessageChain, Session, command

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
    
@leto.on(MessageCreatedEvent)
async def on_message_created(message_chain: MessageChain, session: Session[MessageEvent]):
    command_name_list = [conf.hyw_command_name] if isinstance(conf.hyw_command_name, str) else conf.hyw_command_name
    if session.reply:
        try:
            message_chain.extend(session.reply.origin.message)
        except Exception:
            pass
    message_chain = message_chain.get(Text) + message_chain.get(Image)

    alc = Alconna(command_name_list, Args["all_param;?", AllParam], meta=CommandMeta(compact=True,))
    res = alc.parse(message_chain)
    if not res.matched:
        return

    mc = MessageChain(res.all_param) # type: ignore
    
    async def react(code: str):
        try:
            await session.account.protocol.call_api("internal/set_group_reaction", {
                "group_id": int(session.guild.id),
                "message_id": int(session.event.message.id),
                "code": code,
                "is_add": True
            })
        except ActionFailed:
            # 忽略反应失败的错误
            pass
            
    try:
        # 文本消息(全部)
        msg = mc.get(Text).strip()
        
        images = None
        if mc.get(Image):
            # 下载图片
            urls = mc[Image].map(lambda x: x.src)
            tasks = [agent_service.download_image(url) for url in urls]
            images = await asyncio.gather(*tasks)
        
        # 使用统一入口，传递react函数让AI服务内部处理反应
        res_agent = await agent_service.unified_completion(str(msg), images, react)
        await react("128051")  # 🐳
        
        # 安全检查：处理空回复或被审查的情况
        response_content = str(res_agent.content) if hasattr(res_agent, 'content') else ""
        if not response_content.strip():
            # 检查是否有工具调用但没有内容
            if hasattr(res_agent, 'tool_calls') and res_agent.tool_calls:
                response_content = "[KEY] :: 信息处理 | 内容获取\n>> [search enable]\n抱歉，获取到的内容可能包含敏感信息，暂时无法显示完整结果。\n[LLM] :: 安全过滤"
                raise ValueError("内容被安全过滤，无法显示完整结果。")
            else:
                response_content = "[KEY] :: 系统响应 | 处理异常\n>> [search enable]\n抱歉，暂时无法生成回复内容。\n[LLM] :: 系统提示"
                raise ValueError("内容被安全过滤，无法显示完整结果。")
        
        await session.send(response_content)
    except Exception as e:
        await react("10060")  # ❌
        raise e
