import asyncio
import urllib.parse
import re
import time
from typing import List, Dict, Any, Optional
from loguru import logger

from .browser.service import get_screenshot_service
# New engines
from .browser.engines.bing import BingEngine
from .browser.engines.duckduckgo import DuckDuckGoEngine
from .browser.engines.google import GoogleEngine

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
        elif self._engine_name == "google":
            self._engine = GoogleEngine()
        elif self._engine_name == "duckduckgo":
            self._engine = DuckDuckGoEngine()
        else:
            # Default fallback
            self._engine = BingEngine()
        
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
        Returns parsed search results only.
        """
        if not query:
            return []

        # Apply blocking
        final_query = query
        if self._blocked_domains and "-site:" not in query:
             exclusions = " ".join([f"-site:{d}" for d in self._blocked_domains])
             final_query = f"{query} {exclusions}"

        url = self._build_search_url(final_query)
        logger.info(f"Search: '{query}' -> {url}")

        results = []
        try:
            # Fetch - Search parsing doesn't need screenshot, only HTML
            page_data = await self.fetch_page_raw(url, include_screenshot=False)
            content = page_data.get("html", "") or page_data.get("content", "")

            # Parse Results (skip raw page - only return parsed results)
            if content and not content.startswith("Error"):
                parsed = self._engine.parse(content)

                # JAVASCRIPT IMAGE INJECTION
                # Inject base64 images from JS extraction if available
                # This provides robust fallback if HTTP URLs fail to load
                js_images = page_data.get("images", [])
                if js_images:
                    logger.info(f"Search: Injecting {len(js_images)} base64 images into top results")
                    for i, img_b64 in enumerate(js_images):
                        if i < len(parsed):
                            b64_src = f"data:image/jpeg;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
                            if "images" not in parsed[i]: parsed[i]["images"] = []
                            # Prepend to prioritize base64 (guaranteed render) over HTTP URLs
                            parsed[i]["images"].insert(0, b64_src)

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
