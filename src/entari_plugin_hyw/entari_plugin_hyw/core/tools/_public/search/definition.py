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
            "description": """搜索网页或截图指定URL。用于获取duckduckgo搜索结果或网页内容。
## 使用方式
网页搜索(大部分问题优先使用此方法):
- query: 搜索词，如 "python async"
- kl: 可选，地区/语言（如 us-en、cn-zh）
- time_range: 必填，时间范围代码（a/d/w/m/y；a 表示全时段）

网页截图(当用户明确要求截图时使用):
传入完整URL如 "https://example.com" 会直接截图该页面
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询或网页获取"
                    },
                    "kl": {
                        "type": "string",
                        "description": "可选，DuckDuckGo 区域/语言参数，如 us-en、cn-zh。留空则使用默认。"
                    },
                    "time_range": {
                        "type": "string",
                        "description": "必填，搜索时间范围代码。可用值: a(全时段) / d(近1日) / w(近1周) / m(近1月) / y(近1年)。",
                        "enum": ["a", "d", "w", "m", "y"]
                    }
                },
                "required": ["query", "time_range"]
            }
        }
    }
