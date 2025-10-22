import asyncio
import httpx
import json
from typing import List
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from loguru import logger


# 全局搜索引擎配置
_global_search_engine = "bing"
_global_compressor_llm = None


def set_global_search_engine(engine: str):
    """设置全局搜索引擎"""
    global _global_search_engine
    _global_search_engine = engine


def set_global_compressor_llm(llm: ChatOpenAI):
    """设置全局压缩器LLM"""
    global _global_compressor_llm
    _global_compressor_llm = llm


async def compress_content(content: str, max_length: int = 4000) -> str:
    """压缩内容长度"""
    if len(content) <= max_length:
        return content
    
    if not _global_compressor_llm:
        # 如果没有压缩器，简单截断
        return content[:max_length] + "\n\n[内容过长，已截断]"
    
    try:
        from langchain_core.messages import HumanMessage
        import asyncio
        
        # 对于超长内容，先进行智能截断，再压缩
        if len(content) > 50000:  # 超过50k字符时先截断
            # 按段落分割，保留重要部分
            paragraphs = content.split('\n\n')
            important_content = []
            current_length = 0
            
            for para in paragraphs:
                if current_length + len(para) > 20000:  # 保留前20k字符
                    break
                if len(para.strip()) > 10:  # 跳过太短的段落
                    important_content.append(para)
                    current_length += len(para)
            
            content = '\n\n'.join(important_content)
            logger.info(f"超长内容预处理: {len(content)} -> {len(content)} 字符")
        
        # 设置压缩超时
        prompt = f"""请将以下网页内容压缩到 {max_length} 字符以内，保留最重要的信息：

{content[:10000]}  # 限制prompt长度

要求：
1. 保留核心信息和关键数据
2. 去除冗余内容和重复信息
3. 保持原文的逻辑结构
4. 使用简洁清晰的语言
5. 严格控制字数在 {max_length} 字符以内"""

        messages = [HumanMessage(content=prompt)]
        
        # 使用超时机制
        try:
            result = await asyncio.wait_for(
                _global_compressor_llm.ainvoke(messages), 
                timeout=30.0  # 30秒超时
            )
            compressed_content = result.content
            
            # 检查压缩结果长度
            if len(str(compressed_content)) > max_length * 1.5:  # 如果压缩效果不好
                logger.warning(f"压缩效果不佳，长度仍为 {len(str(compressed_content))}，使用截断方式")
                return str(compressed_content)[:max_length] + "\n\n[内容压缩效果不佳，已截断]"
            
            logger.info(f"内容压缩完成: 原长度 {len(content)} -> 压缩后 {len(str(compressed_content))}")
            return str(compressed_content)
            
        except asyncio.TimeoutError:
            logger.error(f"内容压缩超时，使用截断方式")
            return content[:max_length] + "\n\n[内容压缩超时，已截断]"
        
    except Exception as e:
        logger.error(f"内容压缩失败: {e}")
        # 压缩失败时简单截断
        return content[:max_length] + "\n\n[内容压缩失败，已截断]"


# 智能搜索工具
@tool
async def web_search(queries: List[str]) -> str:
    """
    智能搜索工具, 返回JSON格式文本结果
    queries: 同时完成多个查询任务
    """

    async def ddsg_search(keyword: str, backend: str) -> List[dict]:
        """文本搜索"""
        from ddgs import DDGS
        
        def _sync_search():
            """同步搜索函数，在线程池中执行"""
            logger.info(f"使用 DDGS 搜索: 关键词='{keyword}', 引擎='{backend}'")
            return DDGS().text(
                keyword,
                safesearch="off",
                timelimit="y",
                page=1,
                backend=backend,
            )
        
        # 使用 asyncio.to_thread 在线程池中运行同步操作，不阻塞事件循环
        results = await asyncio.to_thread(_sync_search)
        return results

    try:
        # 执行搜索 - 并发执行所有搜索任务
        async def search_task(query: str, backend: str):
            """搜索任务"""
            results = await ddsg_search(query, backend=backend)
            return {
                "query": query,
                "results": results,
                "search_type": "web"
            }
        
        # 构建所有搜索任务 - 全部使用一般搜索
        search_tasks = [search_task(query, backend=_global_search_engine) for query in queries]
        
        # 并发执行所有搜索任务
        all_results = await asyncio.gather(*search_tasks) if search_tasks else []
    
        
        # logger.info(f"智能搜索结果: {all_results}")
        return json.dumps(all_results, ensure_ascii=False, indent=2)
        
    except Exception as e:
        # 错误处理
        error_results = [
            {
                "query": query,
                "results": [],
                "search_type": "unknown",
                "error": str(e)
            }
            for query in queries
        ]
        logger.error(f"智能搜索失败: {e}")
        return json.dumps(error_results, ensure_ascii=False, indent=2)


@tool
async def exact_search(query: str) -> str:
    """
    精确搜索工具
    - 传入内容必须为一个完整的查询字符串
    - 禁止查询短语、语句、询问
    query: 要精确搜索的关键词（单个查询字符串）
    """
    
    async def ddsg_search(keyword: str, backend: str) -> List[dict]:
        """文本搜索"""
        from ddgs import DDGS
        
        def _sync_search():
            """同步搜索函数，在线程池中执行"""
            logger.info(f"使用 DDGS 精确搜索: 关键词='{keyword}', 引擎='{backend}'")
            return DDGS().text(
                keyword,
                safesearch="off",
                timelimit="y",
                page=1,
                backend=backend,
            )
        
        # 使用 asyncio.to_thread 在线程池中运行同步操作，不阻塞事件循环
        results = await asyncio.to_thread(_sync_search)
        return results

    try:
        # 将查询词用双引号包裹，进行精确搜索
        exact_query = f'"{query}"'
        logger.info(f"执行精确搜索: 原始查询='{query}', 精确查询='{exact_query}'")
        
        results = await ddsg_search(exact_query, backend=_global_search_engine)
        
        result_data = {
            "query": query,
            "exact_query": exact_query,
            "results": results,
            "search_type": "exact"
        }
        return json.dumps(result_data, ensure_ascii=False, indent=2)
        
    except Exception as e:
        # 错误处理
        error_result = {
            "query": query,
            "exact_query": f'"{query}"',
            "results": [],
            "search_type": "exact",
            "error": str(e)
        }
        logger.error(f"精确搜索失败: {e}")
        return json.dumps(error_result, ensure_ascii=False, indent=2)


@tool
async def jina_fetch_webpage(url: str) -> str:
    """
    输入网址, 获取网页内容
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"https://r.jina.ai/{url}")
            if resp.status_code == 200:
                content = resp.text
                logger.info(f"网页获取成功，原始长度: {len(content)} 字符")
                
                # 使用压缩器压缩内容
                compressed_content = await compress_content(content, max_length=4000)
                logger.info(f"网页内容处理完成，最终长度: {len(compressed_content)} 字符")
                
                return compressed_content
            else:
                return f"获取网页失败，状态码: {resp.status_code}"
    except Exception as e:
        return f"获取网页失败: {str(e)}"


@tool
async def nbnhhsh(text: str) -> str:
    """
    用于复原一个缩写的所有可能性
    注意: 此工具会缩写推演所有可能性, 客观存在很多污染
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("https://lab.magiconch.com/api/nbnhhsh/guess", json={"text": text})
            if resp.status_code == 200:
                _res =  json.dumps(resp.json(), ensure_ascii=False, indent=2)
                logger.info(f"nbnhhsh 解释成功: {_res}")
                return _res
            else:
                return f"API请求失败: {resp.status_code}"
    except Exception as e:
        return f"解释失败: {str(e)}"
