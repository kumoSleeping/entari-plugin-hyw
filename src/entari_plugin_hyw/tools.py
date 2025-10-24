import asyncio
import httpx
import json
from typing import List, Optional
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from loguru import logger

from .compressor import WebpageCompressor


# 全局搜索引擎配置
_global_search_engines = ["auto"]

# 全局压缩器配置
_global_compressor: Optional[WebpageCompressor] = None


def set_global_search_engines(engines: List[str]):
    """设置全局搜索引擎列表"""
    global _global_search_engines
    _global_search_engines = engines


def set_global_compressor_llm(llm: ChatOpenAI):
    """设置全局压缩器LLM"""
    global _global_compressor
    _global_compressor = WebpageCompressor(llm)
    logger.info("[工具] 压缩器已初始化")
    
    
    
# 智能搜索工具
@tool
async def web_search(query: str) -> str:
    """
    - web_search 每个查询只能包含一个的关键词建议在 1-2 个词之间 不超过 3 个词
    - 多引擎并发搜索工具，使用配置文件指定的搜索引擎进行搜索
    - 支持同时搜索多个搜索引擎并合并结果
    - 搜索引擎配置在 entari.yml 中的 search_engines 字段，如 ["duckduckgo::exact", "duckduckgo"]，::exact 表示精确搜索
    - 自动去重和合并来自不同搜索引擎的结果，提供更全面的信息
    智能搜索工具, 返回JSON格式文本结果
    query: 要搜索的关键词
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
        # 使用全局配置的搜索引擎列表
        if len(_global_search_engines) == 1:
            logger.info(f"使用单引擎搜索模式: {_global_search_engines[0]}")
            parsed_engines = parse_engines(_global_search_engines)
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
                "engine": _global_search_engines[0],
                "search_mode": "exact" if is_exact else "normal",
                "success": single_result.get("success", False),
                "exact_matched": (len(filtered_results) > 0) if is_exact else None,
                "note": ("没有精确匹配结果" if (is_exact and len(filtered_results) == 0) else None),
                "error": single_result.get("error")
            }
            
            return json.dumps(result_data, ensure_ascii=False, indent=2)
        
        # 使用全局配置的搜索引擎列表进行多引擎搜索
        search_engines = _global_search_engines
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
                "failed": _global_search_engines,
                "total_requested": len(_global_search_engines)
            },
            "merged_results": [],
            "engine_results": {},
            "search_type": "multi_engine",
            "stats": {
                "total_results": 0,
                "successful_engines_count": 0,
                "failed_engines_count": len(_global_search_engines)
            },
            "error": str(e)
        }
        logger.error(f"多引擎搜索失败: {e}")
        return json.dumps(error_result, ensure_ascii=False, indent=2)


@tool
async def jina_fetch_webpage(url: str) -> str:
    """
    输入网址, 获取网页内容
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.info(f"Jina 获取页面: URL='{url}'")
            resp = await client.get(f"https://r.jina.ai/{url}", headers={"X-Engine": "direct"})
            if resp.status_code == 200:
                content = resp.text
                logger.info(f"网页获取成功，原始长度: {len(content)} 字符")
                
                # 如果压缩器已初始化，则使用压缩器压缩内容
                if _global_compressor is not None:
                    try:
                        content = await _global_compressor.compress_webpage(content, url)
                        logger.info(f"网页内容压缩完成，最终长度: {len(content)} 字符")
                    except Exception as e:
                        logger.error(f"网页压缩失败: {e}，返回原始内容")
                
                return content
            else:
                return f"获取网页失败，状态码: {resp.status_code}"
    except Exception as e:
        return f"获取网页失败: {str(e)}"

