import json
from typing import List, Dict, Any
from .._public.search.service import DuckDuckGoSearchService


# 全局序号计数器，用于多次搜索时序号递增
_global_index_counter = 0


def reset_search_index():
    """重置搜索结果序号计数器（每次新对话开始时调用）"""
    global _global_index_counter
    _global_index_counter = 0


async def web_search(query: str, headless: bool = True) -> str:
    """
    执行网页搜索，返回原始结果。

    Returns:
        JSON 格式的搜索结果
    """
    global _global_index_counter

    # 预处理：移除可能导致搜索失败的双引号（包括中文双引号）
    query = query.replace('"', ' ').replace('“', ' ').replace('”', ' ')

    print(f"  [SearchTool] Searching: {query}")
    service = DuckDuckGoSearchService(headless=headless)
    results = await service.search(query)

    if not results:
        return json.dumps({
            "query": query,
            "count": 0,
            "results": []
        }, ensure_ascii=False)

    visible_results = [r for r in results if not r.get("_hidden")][:5]

    # 构建结果列表，使用全局递增序号
    formatted_results = []
    for r in visible_results:
        _global_index_counter += 1
        title = r.get("title", "No Title")
        snippet = r.get("snippet", "") or r.get("content", "")
        url = r.get("url", "")
        snippet = snippet[:300].replace("\n", " ")

        formatted_results.append({
            "index": _global_index_counter,
            "title": title,
            "url": url,
            "snippet": snippet
        })

    output = {
        "query": query,
        "count": len(formatted_results),
        "results": formatted_results
    }

    return json.dumps(output, ensure_ascii=False, indent=2)
