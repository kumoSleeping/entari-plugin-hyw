import asyncio
import httpx
import json
from typing import List
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from loguru import logger


# 全局搜索引擎配置
_global_search_engines = ["auto"] 



def set_global_search_engines(engines: List[str]):
    """设置全局搜索引擎列表"""
    global _global_search_engines
    _global_search_engines = engines


def set_global_compressor_llm(llm: ChatOpenAI):
    """设置全局压缩器LLM"""
    global _global_compressor_llm
    _global_compressor_llm = llm


async def compress_content(content: str, max_length: int = 4000) -> str:
    """使用LLM压缩内容"""
    if len(content) <= max_length:
        return content
    
    if not _global_compressor_llm:
        logger.warning("压缩器LLM未初始化，返回原始内容")
        return content
    
    try:
        from langchain_core.messages import HumanMessage
        
        prompt = f"""请将以下网页内容压缩到 {max_length} 字符以内，保留最重要的信息：

{content}

要求：
1. 保留核心信息和关键数据
2. 去除冗余内容和重复信息
3. 保持原文的逻辑结构
4. 使用简洁清晰的语言
5. 严格控制字数在 {max_length} 字符以内"""

        messages = [HumanMessage(content=prompt)]
        result = await _global_compressor_llm.ainvoke(messages)
        compressed_content = result.content
        
        logger.info(f"内容压缩完成: 原长度 {len(content)} -> 压缩后 {len(str(compressed_content))}")
        return str(compressed_content)
        
    except Exception as e:
        logger.error(f"内容压缩失败: {e}")
        return content


# 智能搜索工具
@tool
async def web_search(query: str, engines: List[str] | None = None) -> str:
    """
    - web_search 每个查询只能包含一个的关键词建议在 1-2 个词之间 不超过 3 个词
    - 多引擎并发搜索工具，支持同时搜索多个搜索引擎并合并结果
    - 可以指定搜索引擎列表，如 ["brave", "brave::exact", "bing"]，::exact 表示精确搜索
    - 自动去重和合并来自不同搜索引擎的结果，提供更全面的信息
    智能搜索工具, 返回JSON格式文本结果
    query: 要搜索的关键词
    engines: 搜索引擎列表，默认使用全局配置的搜索引擎列表
    """

    async def ddsg_search(keyword: str, backend: str, is_exact: bool = False) -> dict:
        """单个搜索引擎搜索"""
        from ddgs import DDGS
        
        def _sync_search():
            # 简化的搜索逻辑
            search_keyword = f'"{keyword}"' if is_exact else keyword
            logger.info(f"DDGS 搜索: 关键词='{search_keyword}', 引擎='{backend}'")
            return DDGS().text(search_keyword, safesearch="off", timelimit="y", max_results=7, backend=backend)
        
        try:
            results = await asyncio.to_thread(_sync_search)
            logger.info(f"搜索完成: {backend} -> {len(results)} 个结果")
            return {"engine": backend, "results": results, "success": True}
        except Exception as e:
            logger.error(f"搜索失败: {backend} -> {e}")
            return {"engine": backend, "results": [], "success": False, "error": str(e)}

    def parse_engines(engines_list: List[str]) -> List[tuple]:
        """解析搜索引擎列表，提取引擎名称和是否精确搜索"""
        parsed = []
        for engine in engines_list:
            if "::exact" in engine:
                base_engine = engine.replace("::exact", "")
                parsed.append((base_engine, True))
            else:
                parsed.append((engine, False))
        return parsed

    try:
        # 如果指定了单个搜索引擎，使用传统单引擎模式
        if engines is not None and len(engines) == 1:
            logger.info(f"使用单引擎搜索模式: {engines[0]}")
            parsed_engines = parse_engines(engines)
            engine_name, is_exact = parsed_engines[0]
            single_result = await ddsg_search(query, engine_name, is_exact)
            # 对精确模式进行客户端二次过滤，确保只保留包含完整查询词的结果
            filtered_results = single_result.get("results", [])
            if is_exact:
                q = (query or "").lower()
                def _hit(res: dict) -> bool:
                    title = (res.get("title") or "").lower()
                    body = (res.get("body") or "").lower()
                    href = (res.get("href") or "").lower()
                    return (q in title) or (q in body) or (q in href)
                filtered_results = [r for r in filtered_results if _hit(r)]
            
            result_data = {
                "query": query,
                "results": filtered_results,
                "search_type": "single_engine",
                "engine": engines[0],
                "search_mode": "exact" if is_exact else "normal",
                "success": single_result.get("success", False),
                "exact_matched": (len(filtered_results) > 0) if is_exact else None,
                "note": ("没有精确匹配结果" if (is_exact and len(filtered_results) == 0) else None),
                "error": single_result.get("error")
            }
            
            return json.dumps(result_data, ensure_ascii=False, indent=2)
        
        # 使用传入的搜索引擎列表或全局默认列表进行多引擎搜索
        search_engines = engines if engines is not None else _global_search_engines
        parsed_engines = parse_engines(search_engines)
        logger.info(f"开始多引擎并发搜索: 查询='{query}', 引擎={search_engines}")
        
        # 并发执行所有搜索引擎的搜索
        search_tasks = [ddsg_search(query, engine_name, is_exact) for engine_name, is_exact in parsed_engines]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        
        # 简化的结果处理
        merged_results = {}
        successful_engines = []
        failed_engines = []
        
        for i, result in enumerate(search_results):
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue
                
            engine_config = parsed_engines[i] if i < len(parsed_engines) else ("unknown", False)
            engine_name, is_exact = engine_config
            display_name = f"{engine_name}(精确)" if is_exact else f"{engine_name}(普通)"
            
            # 简单格式化结果 + 精确模式客户端二次过滤
            raw_results = result.get("results", [])
            if is_exact:
                q = (query or "").lower()
                def _hit(res: dict) -> bool:
                    title = (res.get("title") or "").lower()
                    body = (res.get("body") or "").lower()
                    href = (res.get("href") or "").lower()
                    return (q in title) or (q in body) or (q in href)
                raw_results = [r for r in raw_results if _hit(r)]
                if len(raw_results) == 0:
                    logger.info(f"精确模式无匹配: 查询='{query}', 引擎='{engine_name}'")
            formatted_results = []
            for j, res in enumerate(raw_results):
                formatted_results.append({
                    "title": res.get("title", ""),
                    "href": res.get("href", ""),
                    "body": res.get("body", ""),
                    "source": display_name
                })
            
            # 使用不同的键名来区分同一引擎的不同搜索模式
            result_key = f"{engine_name}_exact" if is_exact else engine_name
            merged_results[result_key] = {
                "results": formatted_results,
                "success": result.get("success", False),
                "display_name": display_name,
                "engine_name": engine_name,
                "is_exact": is_exact,
                "exact_matched": (len(formatted_results) > 0) if is_exact else None,
                "note": ("没有精确匹配结果" if (is_exact and len(formatted_results) == 0) else None)
            }
            
            if result.get("success", False):
                successful_engines.append(result_key)
            else:
                failed_engines.append(result_key)
        
        # 不去重，保留所有结果，让LLM能够看到不同搜索模式的差异
        unique_results = []
        for result in merged_results.values():
            unique_results.extend(result["results"])
        
        # 构建结果
        result_data = {
            "query": query,
            "merged_results": unique_results,
            "engine_results": merged_results,
            "search_type": "multi_engine",
            "stats": {
                "total_results": len(unique_results),
                "successful_engines_count": len(successful_engines),
                "failed_engines_count": len(failed_engines)
            }
        }
        
        logger.info(f"多引擎搜索完成: 成功引擎 {len(successful_engines)}/{len(search_engines)}, 总结果: {unique_results}")
        return json.dumps(result_data, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_result = {
            "query": query,
            "search_engines": {
                "successful": [],
                "failed": engines if engines is not None else _global_search_engines,
                "total_requested": len(engines if engines is not None else _global_search_engines)
            },
            "merged_results": [],
            "engine_results": {},
            "search_type": "multi_engine",
            "stats": {
                "total_results": 0,
                "successful_engines_count": 0,
                "failed_engines_count": len(engines if engines is not None else _global_search_engines)
            },
            "error": str(e)
        }
        logger.error(f"多引擎搜索失败: {e}")
        return json.dumps(error_result, ensure_ascii=False, indent=2)
# @tool
# async def single_word_web_search(query: str) -> str:
#     """
#     精确搜索工具
#     - 禁止出现空格
#     - 传入内容必须为一个完整的查询字符串
#     - 禁止查询短语、语句、询问
#     - 只能用于搜索单个名词或专有名词
#     query: 要精确搜索的关键词（单个查询字符串）
#     """
    
#     async def ddsg_search(keyword: str, backend: str) -> List[dict]:
#         """文本搜索"""
#         from ddgs import DDGS
        
#         def _sync_search():
#             """同步搜索函数，在线程池中执行"""
#             logger.info(f"DDGS 精确搜索: 关键词='{keyword}', 引擎='{backend}'")
#             return DDGS().text(
#                 keyword,
#                 region='cn',
#                 language='zh',
#                 safesearch="off",
#                 timelimit="y",
#                 page=1,
#                 max_results=3,
#                 backend=backend,
#             )
        
#         # 使用 asyncio.to_thread 在线程池中运行同步操作，不阻塞事件循环
#         results = await asyncio.to_thread(_sync_search)
#         return results

#     try:
#         # 将查询词用双引号包裹，进行精确搜索
#         exact_query = f'"{query}"'
#         logger.info(f"执行精确搜索: 原始查询='{query}', 精确查询='{exact_query}'")
        
#         results = await ddsg_search(exact_query, backend=_global_search_engine)
        
#         result_data = {
#             "query": query,
#             "exact_query": exact_query,
#             "results": results,
#             "search_type": "exact"
#         }
#         return json.dumps(result_data, ensure_ascii=False, indent=2)
        
#     except Exception as e:
#         # 错误处理
#         error_result = {
#             "query": query,
#             "exact_query": f'"{query}"',
#             "results": [],
#             "search_type": "exact",
#             "error": str(e)
#         }
#         logger.error(f"精确搜索失败: {e}")
#         return json.dumps(error_result, ensure_ascii=False, indent=2)


@tool
async def jina_fetch_webpage(url: str) -> str:
    """
    输入网址, 获取网页内容
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.info(f"Jina 获取页面: URL='{url}'")
            resp = await client.get(f"https://r.jina.ai/{url}")
            if resp.status_code == 200:
                content = resp.text
                logger.info(f"网页获取成功，原始长度: {len(content)} 字符")
                
                # 使用压缩器压缩内容
                logger.info("开始压缩网页内容...")
                compressed_content = await compress_content(content, max_length=4000)
                logger.info(f"网页内容处理完成，最终长度: {len(compressed_content)} 字符")
                logger.info(f"网页内容预览: {compressed_content[:300]}...")
                
                return compressed_content
            else:
                return f"获取网页失败，状态码: {resp.status_code}"
    except Exception as e:
        return f"获取网页失败: {str(e)}"


# @tool
# async def nbnhhsh(text: str) -> str:
#     """
#     用于复原一个缩写的所有可能性
#     注意: 此工具会缩写推演所有可能性, 客观存在很多污染
#     """
#     try:
#         async with httpx.AsyncClient(timeout=15.0) as client:
#             resp = await client.post("https://lab.magiconch.com/api/nbnhhsh/guess", json={"text": text})
#             if resp.status_code == 200:
#                 _res =  json.dumps(resp.json(), ensure_ascii=False)
#                 logger.info(f"nbnhhsh 解释成功: {_res}")
#                 return _res
#             else:
#                 return f"API请求失败: {resp.status_code}"
#     except Exception as e:
#         return f"解释失败: {str(e)}"
