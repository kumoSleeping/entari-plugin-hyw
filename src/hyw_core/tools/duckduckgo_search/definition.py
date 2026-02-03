"""
DuckDuckGo Search Tool Definition

Tool schema for LLM function calling.
"""

from typing import Dict, Any


def get_duckduckgo_search_tool() -> Dict[str, Any]:
    """Tool for web search with DuckDuckGo and URL screenshot."""
    return {
        "type": "function",
        "function": {
            "name": "web_tool",
            "description": """搜索网页或截图指定URL。用于获取duckduckgo搜索结果或网页内容
## 使用方式
网页搜索(大部分问题优先使用此方法):
直接传入搜索词如 "python async" 会返回搜索结果列表 搜索词尽可能少且精准, 以利于传统搜索引擎检索

网页截图(当用户明确要求截图时使用):
传入完整URL如 "https://example.com" 会直接截图该页面
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询或网页获取"
                    }
                },
                "required": ["query"]
            }
        }
    }
