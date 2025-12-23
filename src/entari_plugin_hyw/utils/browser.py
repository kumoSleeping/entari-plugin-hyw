from typing import Any
from loguru import logger
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import CrawlerRunConfig
from crawl4ai.cache_context import CacheMode


class BrowserTool:
    """Crawl4AI-based page fetcher."""

    def __init__(self, config: Any):
        self.config = config

    async def navigate(self, url: str) -> str:
        """Fetch URL content via Crawl4AI and return markdown."""
        if not url:
            return "Error: missing url"
        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(
                    url=url,
                    config=CrawlerRunConfig(
                        wait_until="networkidle",
                        wait_for_images=True,
                        cache_mode=CacheMode.BYPASS,
                        word_count_threshold=1,
                        screenshot=False,
                    ),
                )
            if not result.success:
                return f"Error navigating to {url}: {result.error_message or result.status_code}"

            content = result.markdown or result.extracted_content or result.cleaned_html or result.html or ""
            return content[:8000]
        except Exception as e:
            logger.error(f"HTTP navigation failed: {e}")
            return f"Error navigating to {url}: {e}"

    async def close(self):
        return None
