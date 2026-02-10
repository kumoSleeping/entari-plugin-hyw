from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class Message:
    role: str
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class AgentResponse:
    content: str
    success: bool = True
    error: Optional[str] = None
    history: List[Message] = field(default_factory=list)
    is_final: bool = False
    should_render: bool = False
    scoring: Optional[List[Dict[str, Any]]] = None  # 评分信息


@dataclass
class ToolResult:
    """统一的工具返回结果"""
    content: str
    should_finish: bool = False  # 是否结束会话
