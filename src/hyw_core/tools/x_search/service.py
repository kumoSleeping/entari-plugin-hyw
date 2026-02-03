"""
X (Twitter) Search Service

Provides search functionality for X (Twitter) by automating browser navigation to search URLs.
"""

import asyncio
from typing import List, Dict, Any, Optional
from loguru import logger
import urllib.parse

from ..browser import BrowserService


class XSearchService:
    """
    Search service for X (Twitter).
    Constructs X search URLs and delegates to BrowserService to fetch/screenshot.
    """

    def __init__(self, headless: bool = True, fetch_timeout: float = 20.0):
        self._headless = headless
        self._fetch_timeout = fetch_timeout
        self._browser = BrowserService(headless=headless, fetch_timeout=fetch_timeout)
        logger.info("XSearchService initialized")

    def _build_search_url(self, query: str, filter_type: str = "top") -> str:
        """
        Construct the X search URL based on query and filter.

        Args:
            query: The search term
            filter_type: 'top', 'live', 'user', or 'media'

        Returns:
            Full X search URL
        """
        base_url = "https://x.com/search"
        params = {
            "q": query,
            "src": "typed_query"
        }

        # Add filter parameter if not default 'top'
        if filter_type == "live":
            params["f"] = "live"
        elif filter_type == "user":
            params["f"] = "user"
        elif filter_type == "media":
            params["f"] = "media"

        return f"{base_url}?{urllib.parse.urlencode(params)}"

    async def search(self, query: str, filter_type: str = "top") -> Dict[str, Any]:
        """
        Execute a single X search and return the page content/screenshot.

        Args:
            query: Search term
            filter_type: Filter type ('top', 'live', 'user', 'media')

        Returns:
            Dictionary containing url, screenshot, content, etc.
        """
        if not query:
            return {"error": "Query is required"}

        url = self._build_search_url(query, filter_type)
        logger.info(f"X Search: '{query}' ({filter_type}) -> {url}")

        try:
            # We use fetch_page which returns title, content, screenshot, etc.
            # X is a SPA, so standard HTML fetching might be limited, but screenshot is key here.
            # We might need longer timeout for X to load
            result = await self._browser.fetch_page(url, timeout=self._fetch_timeout + 5, include_screenshot=True)

            # Enrich result with search metadata
            result["search_query"] = query
            result["search_filter"] = filter_type
            result["tool"] = "x_search"

            return result

        except Exception as e:
            logger.error(f"X Search error for '{query}': {e}")
            return {
                "url": url,
                "error": str(e),
                "search_query": query
            }

    async def search_batch(self, queries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Execute multiple searches concurrently.

        Args:
            queries: List of dicts with 'query' and optional 'filter_type'

        Returns:
            List of result dictionaries
        """
        # Limit to 2 concurrent searches as per requirement
        max_concurrent = 2
        semaphore = asyncio.Semaphore(max_concurrent)

        async def bounded_search(q_data):
            async with semaphore:
                return await self.search(
                    q_data.get("query"),
                    q_data.get("filter_type", "top")
                )

        tasks = [bounded_search(q) for q in queries]
        return await asyncio.gather(*tasks)
