from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from loguru import logger


class WebpageCompressor:
    """网页内容压缩器"""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
    
    async def compress_webpage(self, webpage_content: str, url: str) -> str:
        """使用LLM压缩网页内容"""
        if self.llm is None:
            raise RuntimeError("压缩器LLM未初始化")
        
        logger.info(f"[压缩器] 开始压缩网页内容，原始长度: {len(webpage_content)} 字符")
        logger.debug(f"[压缩器] 网页URL: {url}")
        
        compress_prompt = f"""你是网页内容提取专家，需要从网页中提取核心信息。

[任务]
从以下网页内容中提取主要信息，去除导航、广告、样板文字等噪音。

[网页URL]
{url}

[网页内容]
{webpage_content[:10000]}  # 限制输入长度

[提取要求]
1. 提取网页的主要内容和核心信息
2. 保留关键段落、标题、列表
3. 去除导航栏、页脚、广告、社交媒体按钮等
4. 保留重要的数据、日期、引用
5. 如果是新闻/文章，提取标题、作者、发布时间、正文摘要
6. 如果是百科，s提取定义、关键属性、分类信息
7. 保持信息的逻辑结构

[输出格式]
网页: {url}
---
<压缩后的核心内容>
"""
        
        logger.info(f"[压缩器] 调用LLM压缩，prompt长度: {len(compress_prompt)} 字符")
        result = await self.llm.ainvoke([
            SystemMessage(content=compress_prompt)
        ])
        
        compressed = str(result.content) if hasattr(result, 'content') else str(result)
        logger.info(f"[压缩器] 压缩完成: {len(webpage_content)} -> {len(compressed)} 字符 (压缩率: {100 - len(compressed)*100//len(webpage_content)}%)")
        logger.debug(f"[压缩器] 压缩结果预览: {compressed[:300]}...")
        return compressed
