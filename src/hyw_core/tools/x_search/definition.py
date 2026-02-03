"""
X (Twitter) Search Tool Definition

Tool schema for LLM function calling.
"""

from typing import Dict, Any


def get_x_search_tool() -> Dict[str, Any]:
    """Tool for searching X (Twitter)."""
    return {
        "type": "function",
        "function": {
            "name": "x_search",
            "description": """Search X (Twitter) for posts, users, or media.
Supports basic search and filtered searches (users, media).
Returns a screenshot of the search results page.
Use this tool when specifically asked to search X/Twitter or find tweets/users on X.
NOTE: Maximum 2 concurrent instances allowed by system policy.
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g., 'kumosleeping', 'python async')"
                    },
                    "filter_type": {
                        "type": "string",
                        "enum": ["top", "live", "user", "media"],
                        "description": "Filter type: 'top' (default), 'live' (latest), 'user' (people), 'media' (photos/videos)",
                        "default": "top"
                    }
                },
                "required": ["query"]
            }
        }
    }
