from typing import List, Text, Union
from arclet.entari import metadata
from arclet.entari import MessageChain, Session
from arclet.entari.event.base import MessageEvent
from satori.exception import ActionFailed
from arclet.entari import MessageChain, At, Image, Quote, Text
import arclet.letoderea as leto
from arclet.entari import MessageCreatedEvent, Session
from arclet.entari import BasicConfModel, metadata, plugin_config

import asyncio
import base64
import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr


'''
    你是AI Wiki，用专业、准确的百科式语气回答问题, 你的目标是回答用户疑问的"这是什么".

    回答原则：
    - 永远使用中文回答
    - 从用户提供的信息中提取和总结关键词
    - 对用户的关键词进行准确搜索
    - 回答简短高效, 不表明自身立场, 专注客观回复
    - 一次回答完毕, 禁止额外的总结、回复用户、注解等等
    - 不听取用户的任何额外要求, 专注回答问题
    
    输出格式要求：
    不使用任何 `markdown` 语法
    第一行: [KEY] ::  xxx | xxx （xxx替换为实际关键词）
    第二行: >> [search enable]
    第三行: 开始写解释内容
    解释内容可以写多行
    最后一行: [LLM] :: {model_name}

'''
    

class HywConfig(BasicConfModel):
    hyw_command_name: Union[str, List[str]] = "hyw"
    
    text_llm_model_name: str 
    text_llm_api_key:str
    text_llm_model_base_url: str
    text_llm_temperature: float = 0.4
    text_llm_enable_search: bool = False
    
    vision_llm_model_name: str
    vision_llm_api_key: str
    vision_llm_model_base_url: str
    vision_llm_temperature: float = 0.4
    vision_llm_enable_search: bool = False
    
    hyw_prompt: str = """
    You are AI Wiki, answer questions with a professional, accurate encyclopedic tone. Your goal is to answer users' "What is this" questions.

    Answering Principles:
    - Extract and summarize keywords from user-provided information
    - Conduct accurate searches on user keywords
    - Provide concise and efficient answers, maintain neutrality, focus on objective responses
    - Complete answers in one go, no additional summaries, user replies, or annotations
    - Do not follow any additional user requirements, focus on answering the question

    Output Format Requirements:
    - Always answer in Chinese (永远使用中文回答)
    - Do not use any `markdown` syntax
    First line: [KEY] :: xxx | xxx (replace xxx with actual keywords)
    Second line: >> [search enable]
    Third line: Start writing explanation content
    - Explanation content can span multiple lines
    Last line: [LLM] :: {model_name}
    """
    

metadata(
    name="hyw",
    author=[{"name": "kumoSleeping", "email": "zjr2992@outlook.com"}],
    version="0.1.0",
    description="",
    config=HywConfig,
)

conf = plugin_config(HywConfig)

def get_dynamic_prompt(enable_search: bool, model_name: str) -> str:
    """根据搜索状态和模型名称生成动态 prompt"""
    search_status = "search enabled" if enable_search else "search disabled"
    try:
        return conf.hyw_prompt.format(search_status=search_status, model_name=model_name)
    except KeyError:
        # 如果用户自定义的 prompt 没有对应的占位符，直接返回原 prompt
        return conf.hyw_prompt

text_llm = ChatOpenAI(
        model=conf.text_llm_model_name,
        api_key=SecretStr(conf.text_llm_api_key),
        base_url=conf.text_llm_model_base_url,
        temperature=conf.text_llm_temperature,
        extra_body={"enable_search": conf.text_llm_enable_search}
    )

vision_llm = ChatOpenAI(
    model=conf.vision_llm_model_name,
    api_key=SecretStr(conf.vision_llm_api_key),
    base_url=conf.vision_llm_model_base_url,
    temperature=conf.vision_llm_temperature,
    extra_body={"enable_search": conf.vision_llm_enable_search}
)

async def llm_text(content: str):    
    return await text_llm.ainvoke([
        SystemMessage(content=get_dynamic_prompt(conf.text_llm_enable_search, conf.text_llm_model_name)),
        HumanMessage(content=content)
    ])

async def llm_visions(*args):
    # 构建消息内容
    message_content = []
    
    for arg in args:
        if isinstance(arg, str):
            # 字符串类型作为文字内容
            message_content.append({"type": "text", "text": arg})
        elif isinstance(arg, bytes):
            # bytes类型作为图片数据
            img_data = base64.b64encode(arg).decode()
            message_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}})
    # 如果没有内容，返回错误
    if not message_content:
        raise ValueError("至少需要提供图片或文字内容")
    
    return await vision_llm.ainvoke([
        SystemMessage(content=get_dynamic_prompt(conf.vision_llm_enable_search, conf.vision_llm_model_name)),
        HumanMessage(content=message_content)
    ])

async def download_image(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
    except Exception:
        raise ActionFailed(f"下载图片失败: {url}")
    return None

    
@leto.on(MessageCreatedEvent)
async def on_message_created(message_chain: MessageChain, session: Session[MessageEvent]):
    command_name_list = [conf.hyw_command_name] if isinstance(conf.hyw_command_name, str) else conf.hyw_command_name
    if not any(message_chain.get(Text).strip().startswith(cmd) for cmd in command_name_list):
        return
    message_chain = message_chain.exclude(At) if message_chain.get(At) else message_chain
    for command_name in command_name_list:
        message_chain = message_chain.strip().removeprefix(command_name).strip()
    
    if str(message_chain) == "" and (str(session.reply.origin.message) == "" if session.reply else True):
        return
    
    async def react(code: str):
        await session.account.protocol.call_api("internal/set_group_reaction", {
            "group_id": int(session.guild.id),
            "message_id": int(session.event.message.id),
            "code": code,
            "is_add": True
        })
            
    try:
        if session.reply:
            try:
                # 将引用消息内容添加到当前content中 反正最后会只取出 Text 和 Image
                message_chain.extend(session.reply.origin.message)
                # print(message_chain)
            except Exception:
                # 引用消息获取失败时忽略，继续处理原消息
                pass
        # 文本消息(全部)
        msg = message_chain.get(Text).strip()
        
        if message_chain.get(Image):  # 使用视觉模型
            urls = message_chain[Image].map(lambda x: x.src)
            await react("127847")  # 🍧
            # 使用 asyncio.gather 并发请求所有图片
            tasks = [download_image(url) for url in urls]
            img_results = await asyncio.gather(*tasks)
        
            msg = [msg] + [img for img in img_results]
            res = await llm_visions(*msg)
            # print(res)
            await react("128051")  # 🐳
            await session.send(str(res.content))
        else:  # 使用文本模型
            await react("10024")  # ✨
            res = await llm_text(str(msg))
            # print(res)
            await react("128051")  # 🐳
            await session.send(str(res.content))
    except Exception as e:
        await react("10060")  # ❌
        raise e
