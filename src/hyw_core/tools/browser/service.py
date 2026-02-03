"""
Browser Service

Provides browser automation: page fetching, screenshots, and JS execution.
"""

import asyncio
from typing import List, Dict, Any, Optional
from loguru import logger

from ...browser_control.service import get_screenshot_service


class BrowserService:
    """
    Browser automation service.
    Handles page fetching, screenshots, and JS execution.
    """

    def __init__(self, headless: bool = True, fetch_timeout: float = 20.0):
        self._headless = headless
        self._fetch_timeout = fetch_timeout
        logger.info("BrowserService initialized")

    async def fetch_pages_batch(self, urls: List[str], include_screenshot: bool = True) -> List[Dict[str, Any]]:
        """Fetch multiple pages concurrently."""
        tasks = [self.fetch_page(u, include_screenshot=include_screenshot) for u in urls]
        return await asyncio.gather(*tasks)

    async def fetch_page(self, url: str, timeout: Optional[float] = None, include_screenshot: bool = True) -> Dict[str, Any]:
        """Fetch a single page for reading/extracting content."""
        if timeout is None:
            timeout = self._fetch_timeout
        return await self.fetch_page_raw(url, timeout, include_screenshot=include_screenshot)

    async def fetch_page_raw(self, url: str, timeout: Optional[float] = None, include_screenshot: bool = True) -> Dict[str, Any]:
        """Internal: Get raw data from browser service."""
        if timeout is None:
            timeout = self._fetch_timeout
        service = get_screenshot_service(headless=self._headless)
        return await service.fetch_page(url, timeout=timeout, include_screenshot=include_screenshot)

    async def screenshot_url(self, url: str, full_page: bool = True) -> Optional[str]:
        """Capture a screenshot of a URL."""
        service = get_screenshot_service(headless=self._headless)
        return await service.screenshot_url(url, full_page=full_page)

    async def screenshot_with_content(self, url: str, max_content_length: int = 8000) -> Dict[str, Any]:
        """Capture screenshot and extract page content."""
        service = get_screenshot_service(headless=self._headless)
        return await service.screenshot_with_content(url, max_content_length=max_content_length)

    async def screenshot_urls_batch(self, urls: List[str], full_page: bool = True) -> List[Optional[str]]:
        """Capture screenshots of multiple URLs concurrently."""
        service = get_screenshot_service(headless=self._headless)
        return await service.screenshot_urls_batch(urls, full_page=full_page)

    async def execute_script(self, script: str) -> Dict[str, Any]:
        """Execute JavaScript in the current page context."""
        service = get_screenshot_service(headless=self._headless)
        return await service.execute_script(script)
