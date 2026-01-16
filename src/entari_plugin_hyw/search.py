import asyncio
import urllib.parse
import re
import time
from typing import List, Dict, Any, Optional
from loguru import logger

from .browser.service import get_screenshot_service
# New engines
from .browser.engines.bing import BingEngine
from .browser.engines.searxng import SearXNGEngine

class SearchService:
    def __init__(self, config: Any):
        self.config = config
        self._headless = getattr(config, "headless", True)
        self._fetch_timeout = getattr(config, "fetch_timeout", 20.0)
        self._default_limit = getattr(config, "search_limit", 10)
        
        # Domain blocking
        self._blocked_domains = getattr(config, "blocked_domains", []) or []
        
        # Select Engine
        self._engine_name = getattr(config, "search_engine", "bing").lower()
        if self._engine_name == "bing":
            self._engine = BingEngine()
        else:
            self._engine = SearXNGEngine()
        
        logger.info(f"SearchService initialized with engine: {self._engine_name}")

    def _build_search_url(self, query: str) -> str:
        return self._engine.build_url(query, self._default_limit)

    async def search_batch(self, queries: List[str]) -> List[List[Dict[str, Any]]]:
        """Execute multiple searches concurrently."""
        tasks = [self.search(q) for q in queries]
        return await asyncio.gather(*tasks)

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Main search entry point. 
        Returns parsed results + 1 raw page item (marked hidden).
        """
        if not query:
            return []

        # Apply blocking
        final_query = query
        enable_blocking = getattr(self.config, "enable_domain_blocking", True)
        if enable_blocking and self._blocked_domains and "-site:" not in query:
             exclusions = " ".join([f"-site:{d}" for d in self._blocked_domains])
             final_query = f"{query} {exclusions}"

        url = self._build_search_url(final_query)
        logger.info(f"Search: '{query}' -> {url}")

        results = []
        try:
            # Fetch - Search parsing doesn't need screenshot, only HTML
            page_data = await self.fetch_page_raw(url, include_screenshot=False)
            content = page_data.get("html", "") or page_data.get("content", "")
            
            # 1. Add Raw Page Item (Always)
            # This allows history manager to save the raw search page for debugging
            raw_item = {
                "title": f"Raw Search: {query}",
                "url": url,
                "content": content,     # Keep original content
                "type": "search_raw_page",   # Special type for history
                "_hidden": False,        # Unhidden to allow LLM access if needed
                "query": query,
                "images": page_data.get("images", [])
            }
            results.append(raw_item)

            # 2. Parse Results
            if content and not content.startswith("Error"):
                parsed = self._engine.parse(content)
                logger.info(f"Search parsed {len(parsed)} results for '{query}' using {self._engine_name}")
                results.extend(parsed)
            else:
                logger.warning(f"Search failed/empty for '{query}': {content[:100]}")

            return results

        except Exception as e:
            logger.error(f"Search error for '{query}': {e}")
            # Ensure we return at least an error item
            return [{
                "title": f"Error Search: {query}",
                "url": url,
                "content": f"Error: {e}",
                "type": "search_raw_page",
                "_hidden": True
            }]

    async def fetch_pages_batch(self, urls: List[str], include_screenshot: bool = True) -> List[Dict[str, Any]]:
        """Fetch multiple pages concurrently."""
        tasks = [self.fetch_page(u, include_screenshot=include_screenshot) for u in urls]
        return await asyncio.gather(*tasks)

    async def fetch_page(self, url: str, timeout: Optional[float] = None, include_screenshot: bool = True) -> Dict[str, Any]:
        """
        Fetch a single page for reading/extracting content.
        """
        if timeout is None:
            timeout = self._fetch_timeout
        return await self.fetch_page_raw(url, timeout, include_screenshot=include_screenshot)

    async def fetch_page_raw(self, url: str, timeout: Optional[float] = None, include_screenshot: bool = True) -> Dict[str, Any]:
        """Internal: Get raw data from browser service."""
        if timeout is None:
            timeout = self._fetch_timeout
        service = get_screenshot_service(headless=self._headless)
        return await service.fetch_page(url, timeout=timeout, include_screenshot=include_screenshot)
