"""
Centralized Definitions

All global prompts and tool definitions for the pipeline stages.
"""

from typing import Dict, Any

# Used by SummaryStage - language appended at runtime
SUMMARY_REPORT_SP = """# 你是一个总结助手 (Agent), 你的职责是基于搜索工具给出的信息，回答用户的问题或解释用户问题中的关键词。
## 核心原则
最小限度使用自身知识, 尽可能使用 web_tool 获取信息.

## 工具使用指南
- 适当时候调用 `refuse_answer`

## 回答格式
- `# ` 大标题约 8-10 个字
- <summary>...</summary> 约 100 字的概括
- 二级标题 + markdown 正文
- 正文使用 [1] 格式引用信息来源, 无需写出源, 系统自动渲染
"""

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


def get_web_tool() -> Dict[str, Any]:
    """Tool for web search with filter syntax and URL screenshot."""
    return {
        "type": "function",
        "function": {
            "name": "web_tool",
            "description": """搜索网页或截图指定URL。用于获取最新信息、查找资料。
网页搜索(大部分问题优先使用此方法): 
直接传入搜索词如 "python async" 会返回搜索结果列表

网页截图(当用户明确要求截图时使用):
传入完整URL如 "https://example.com" 会直接截图该页面

网页搜索 + 网页截图(可以预测能直接搜到什么样的结果时使用): (最终截图最多3张)
- 域名过滤: "github=2: python async" → 会搜索 "python async github" 并截图 链接/标题包含 "github" 的前2个结果
- 序号选择: "1,2: minecraft mods" → 会搜索 "minecraft mods" 并截图第1、2个结果
- 多域名: "mcmod=1, github=1: forge mod" → 会搜索 "forge mod mcmod github" 并截图 链接/标题包含 "mcmod" 的前1个结果和 链接/标题包含 "github" 的前1个结果
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询或URL。支持过滤器语法(见描述)"
                    }
                },
                "required": ["query"]
            }
        }
    }


# =============================================================================
# AGENT PROMPTS
# =============================================================================

AGENT_SYSTEM_PROMPT = """# 你是一个智能助手 (Agent), 你的职责是使用 `web_tool` 工具来帮助用户搜索网页或截图URL, 同时完成用户分配给你的任务.
## 任务
理解用户意图分配给你的任务.
如果用户没有明确分配任务, 则默认任务为解释用户问题中的关键词.

## 核心原则
最小限度使用自身知识, 尽可能使用 web_tool 获取信息.

## 工具使用指南
- 积极使用 web_tool 获取信息
    - 搜索时, 关键词保证简单、指向准确、利于传统搜索引擎.
    - 获取页面截图时, 只使用官方性较强的 wiki、官方网站、资源站等等, 不使用第三方转载新闻网站.
- 最多可调用2次工具, 之后必须给出最终回答
- 适当时候调用 `refuse_answer`
- 对于具体任务, 如果是转述、格式化、翻译等, 请直接给出最终回答, 不再调用工具

## 回答格式
- `# ` 大标题约 8-10 个字
- <summary>...</summary> 约 100 字的概括
- 二级标题 + markdown 正文
- 正文使用 [1] 格式引用信息来源, 无需写出源, 系统自动渲染
"""


