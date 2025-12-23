from typing import Any
from loguru import logger
from crawl4ai.async_configs import CrawlerRunConfig
from crawl4ai.cache_context import CacheMode
from .search import get_shared_crawler


class PlaywrightTool:
    """
    Backwards-compatible wrapper now powered by Crawl4AI.
    """
    def __init__(self, config: Any):
        self.config = config

    async def navigate(self, url: str) -> str:
        if not url:
            return "Error: Missing url"

        try:
            crawler = await get_shared_crawler()
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
                return f"Error: crawl failed ({result.error_message or result.status_code})"
            return (result.markdown or result.extracted_content or result.cleaned_html or result.html or "")[:8000]
        except Exception as e:
            logger.warning(f"Crawl navigation failed: {e}")
            return f"Error: Crawl navigation failed: {e}"
