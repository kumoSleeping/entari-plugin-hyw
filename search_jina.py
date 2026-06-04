import json
import httpx
from .jina import JinaSearchService

def reset_search_index():
    """重置搜索结果序号计数器（每次新对话开始时调用）"""
    return None


async def web_search(query: str, kl: str = "", time_range: str = "", headless: bool = True) -> str:
    """
    执行网页搜索，返回原始结果。

    Returns:
        JSON 格式的搜索结果
    """
    # 预处理：移除可能导致搜索失败的双引号（包括中文双引号）
    query = query.replace('"', ' ').replace('“', ' ').replace('”', ' ')

    print(f"  [SearchTool] Searching: {query}")
    service = JinaSearchService()
    try:
        results = await service.search(query)
    except httpx.TimeoutException as e:
        return json.dumps({
            "query": query,
            "count": 0,
            "results": [],
            "error": f"Jina search timed out: {type(e).__name__}"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "query": query,
            "count": 0,
            "results": [],
            "error": str(e) or type(e).__name__
        }, ensure_ascii=False)

    if not results:
        return json.dumps({
            "query": query,
            "count": 0,
            "results": []
        }, ensure_ascii=False)

    visible_results = [r for r in results if not r.get("_hidden")][:5]

    # 构建结果列表，使用全局递增序号
    formatted_results = []
    for index, r in enumerate(visible_results, start=1):
        title = r.get("title", "No Title")
        snippet = r.get("snippet", "") or r.get("content", "")
        url = r.get("url", "")
        snippet = snippet[:300].replace("\n", " ")

        formatted_results.append({
            "index": index,
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
