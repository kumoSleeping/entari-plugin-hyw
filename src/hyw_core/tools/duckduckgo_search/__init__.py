"""
DuckDuckGo Search Tool

Provides web search functionality using DuckDuckGo Lite.
"""

from .engine import DuckDuckGoEngine
from .definition import get_duckduckgo_search_tool
from .service import DuckDuckGoSearchService, SearchService

__all__ = [
    "DuckDuckGoEngine",
    "get_duckduckgo_search_tool",
    "DuckDuckGoSearchService",
    "SearchService",
]
