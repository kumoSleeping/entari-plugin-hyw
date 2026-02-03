"""
JavaScript Executor Tool Definition

Tool schema for LLM function calling.
"""

from typing import Dict, Any


def get_js_executor_tool() -> Dict[str, Any]:
    """Tool for executing JavaScript in the browser."""
    return {
        "type": "function",
        "function": {
            "name": "js_executor",
            "description": """执行JavaScript代码并返回结果。
代码将在当前浏览器页面的上下文中执行。
注意：
1. 必须使用 `return` 语句返回结果，或者直接作为表达式（如 `1+1`）。
2. 严禁使用 `console.log`，其输出无法被捕获，会导致返回 None。
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "要执行的JavaScript代码字符串"
                    }
                },
                "required": ["script"]
            }
        }
    }
