import base64
from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger


async def vision_expert_analysis(vision_llm: ChatOpenAI, image_data: bytes, query: str = "") -> str:
    """视觉专家分析工具"""
    logger.info("调用视觉专家分析")
    try:
        img_data = base64.b64encode(image_data).decode()
        message_content = [
            {"type": "text", "text": f"请分析这张图片内容。用户问题：{query}" if query else "请详细分析这张图片的内容"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}}
        ]
        
        vision_prompt = """你是视觉分析专家，请分析图片内容：

- 一大段话详尽的描述主要内容, 
- 如果出现文字, 请给出所有文字内容

输出格式：
第一张图描述了... 此外...
...
"""
        
        result = await vision_llm.ainvoke([
            SystemMessage(content=vision_prompt),
            HumanMessage(content=message_content)
        ])
        logger.info(f"视觉专家分析完成: {result}")
        return str(result.content) if hasattr(result, 'content') else str(result)
    except Exception as e:
        return f"视觉分析失败: {str(e)}"
