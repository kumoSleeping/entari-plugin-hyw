import asyncio
import urllib.parse
from typing import Any, Optional

import httpx
import trafilatura
from loguru import logger


class BrowserTool:
    """Simple HTTP fetcher for search and page content."""

    def __init__(self, config: Any):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(8.0)
            self._client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                verify=False
            )
        return self._client

    async def navigate(self, url: str) -> str:
        """Fetch URL content via HTTP and extract markdown."""
        try:
            client = await self._ensure_client()
            resp = await client.get(url)
            if resp.status_code >= 400:
                logger.error(f"HTTP navigation failed status={resp.status_code} url={url}")
                return f"Error navigating to {url}: {resp.status_code}"
            
            html = resp.text
            content = await asyncio.to_thread(
                trafilatura.extract,
                html,
                include_links=True,
                include_images=True,
                include_tables=True,
                output_format="markdown",
            )
            if not content:
                content = html[:4000]
            return content
        except Exception as e:
            logger.error(f"HTTP navigation failed: {e}")
            return f"Error navigating to {url}: {e}"

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

