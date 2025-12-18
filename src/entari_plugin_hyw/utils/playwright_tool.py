import asyncio
from typing import Any, Optional

import trafilatura
from loguru import logger

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover
    async_playwright = None


class PlaywrightTool:
    def __init__(self, config: Any):
        self.config = config

    async def navigate(self, url: str) -> str:
        if not url:
            return "Error: Missing url"
        if async_playwright is None:
            return "Error: Playwright is not available in this environment."

        headless = bool(getattr(self.config, "headless", True))
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=headless)
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                html = await page.content()
                await context.close()
                await browser.close()

            content = await asyncio.to_thread(
                trafilatura.extract,
                html,
                include_links=True,
                include_images=True,
                include_tables=True,
                output_format="markdown",
            )
            return content or html[:4000]
        except Exception as e:
            logger.warning(f"Playwright navigation failed: {e}")
            return f"Error: Playwright navigation failed: {e}"

