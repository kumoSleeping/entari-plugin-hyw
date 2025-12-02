import asyncio
import html
import json
import urllib.parse
from typing import Any, Dict, Optional, Union

import httpx
import trafilatura
from loguru import logger
from playwright.async_api import async_playwright

class BrowserTool:
    def __init__(self, config: Any):
        self.config = config
        self.playwright: Optional[Any] = None
        self.browser: Optional[Any] = None

    async def navigate(self, url: str) -> str:
        """Navigate to a URL and return the page content with fallback mechanism"""
        # Determine primary and secondary methods
        can_use_playwright = True
        
        if self.config.browser_tool == "jina":
            primary, p_name = self._navigate_jina, "Jina"
            secondary, s_name = (self._navigate_playwright, "Playwright") if can_use_playwright else (None, None)
        else: # Default to Playwright or unknown
            primary, p_name = self._navigate_playwright, "Playwright"
            secondary, s_name = self._navigate_jina, "Jina"
            
        # Try primary method
        content = await primary(url)
        
        # Check for failure and fallback
        if content.startswith("Error") and secondary and self.config.enable_browser_fallback:
            logger.warning(f"{p_name} failed: {content}. Falling back to {s_name}...")
            content = await secondary(url)
             
        return content

    async def _navigate_jina(self, url: str) -> str:
        """Navigate using Jina AI"""
        try:
            logger.info(f"Jina AI navigating to: {url}")
            headers = {"Authorization": f"Bearer {self.config.jina_api_key}"} if self.config.jina_api_key else {}
            # Increased timeout to 60s as requested/observed
            async with httpx.AsyncClient(timeout=float(self.config.jina_timeout)) as client:
                resp = await client.get(f"https://r.jina.ai/{url}", headers=headers)
                if resp.status_code == 200:
                    return resp.text
                return f"Error navigating to {url} via Jina: Status {resp.status_code}"
        except httpx.TimeoutException:
            logger.error(f"Jina navigation timed out for {url}")
            return f"Error: Jina navigation timed out for {url}"
        except Exception as e:
            logger.error(f"Jina navigation failed: {e!r}")
            return f"Error navigating to {url} via Jina: {e!r}"

    async def _ensure_browser(self):
        """Ensure Playwright browser is initialized"""
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        
        if self.browser is None:
            self.browser = await self.playwright.chromium.launch(
                headless=self.config.headless,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"]
            )
            logger.info("Playwright browser initialized")

    async def _navigate_playwright(self, url: str) -> str:
        """Navigate using Playwright with a fresh context/page"""
        await self._ensure_browser()
        if not self.browser:
            return "Error: Browser not initialized"

        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        
        # Inject script to hide navigator.webdriver
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        
        page = await context.new_page()
        try:
            logger.info(f"Playwright navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            html_content = await page.content()
            content = trafilatura.extract(
                html_content,
                include_links=False,
                include_images=False,
                include_tables=False,
                include_comments=False,
                output_format="markdown"
            )
            
            if not content:
                content = await page.evaluate("() => document.body.innerText")
                
            return content if content else "Error: Empty content"
        except Exception as e:
            logger.error(f"Playwright navigation failed: {e}")
            return f"Error navigating to {url}: {str(e)}"
        finally:
            await page.close()
            await context.close()
            
    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def search(self, query: str) -> str:
        """Search using Jina AI or fallback to browser navigation"""
        encoded_query = urllib.parse.quote(query)
        
        # Try Jina Search first
        try:
            url = f"https://s.jina.ai/{encoded_query}"
            logger.info(f"Jina AI searching: {query}")
            headers = {"Authorization": f"Bearer {self.config.jina_api_key}"} if self.config.jina_api_key else {}
                
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"Jina search failed with status {resp.status_code}, falling back")
        except Exception as e:
            logger.error(f"Jina search failed: {e}")

        # Fallback: Navigate to search engine (Bing)
        try:
            search_url = f"https://www.bing.com/search?q={encoded_query}"
            logger.info(f"Fallback searching via Bing: {search_url}")
            return await self.navigate(search_url)
        except Exception as e:
             return f"Error searching '{query}': {str(e)}"

    async def screenshot(self, url: str, output_path: str) -> bool:
        """Take a screenshot of a URL using Playwright"""
        await self._ensure_browser()
        if not self.browser:
            logger.error("Browser not initialized for screenshot")
            return False

        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        try:
            logger.info(f"Taking screenshot of: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Wait for fonts
            try:
                await page.evaluate("document.fonts.ready")
            except Exception: pass
            
            # Wait for images to load
            try:
                await page.evaluate("""
                    async () => {
                        const selectors = Array.from(document.querySelectorAll("img"));
                        await Promise.all(selectors.map(img => {
                            if (img.complete) return;
                            return new Promise((resolve, reject) => {
                                img.addEventListener("load", resolve);
                                img.addEventListener("error", resolve);
                            });
                        }));
                    }
                """)
            except Exception: pass
            
            await page.screenshot(path=output_path, full_page=True)
            return True
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return False
        finally:
            await page.close()
            await context.close()
