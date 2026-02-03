"""
Refuse Answer Tool Definition

Tool schema for LLM function calling.
"""

from typing import Dict, Any


def get_refuse_answer_tool() -> Dict[str, Any]:
    """Tool for refusing to answer inappropriate content."""
    return {
        "type": "function",
        "function": {
            "name": "refuse_answer",
            "description": "违规内容拒绝回答，内容涉及隐喻政治事件、任务、现役国家领导人、r18+、r18g(但不包含正常galgame、科普等)",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "拒绝回答的原因（展示给用户）"},
                },
                "required": ["reason"],
            },
        },
    }
