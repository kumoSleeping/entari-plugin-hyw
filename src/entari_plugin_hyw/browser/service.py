"""
Browser Service (DrissionPage)

Provides page fetching and screenshot capabilities using DrissionPage.
"""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List
from loguru import logger
import trafilatura

class ScreenshotService:
    """
    Browser Service using DrissionPage.
    """
    
    def __init__(self, headless: bool = True, auto_start: bool = True):
        self.headless = headless
        self._manager = None
        self._executor = ThreadPoolExecutor(max_workers=10)
        
        if auto_start:
            self._ensure_ready()
    
    def _ensure_ready(self):
        """Ensure shared browser is ready."""
        from .manager import get_shared_browser_manager
        self._manager = get_shared_browser_manager(headless=self.headless)

    async def fetch_page(self, url: str, timeout: float = 20.0, include_screenshot: bool = True) -> Dict[str, Any]:
        """
        Fetch page content (and optionally screenshot).
        Runs in a thread executor to avoid blocking the async loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._fetch_page_sync,
            url,
            timeout,
            include_screenshot
        )

    def _fetch_page_sync(self, url: str, timeout: float, include_screenshot: bool) -> Dict[str, Any]:
        """Synchronous fetch logic."""
        if not url:
            return {"content": "Error: missing url", "title": "Error", "url": ""}
        
        tab = None
        try:
            self._ensure_ready()
            page = self._manager.page
            if not page:
                return {"content": "Error: Browser not available", "title": "Error", "url": url}
            
            # New Tab
            tab = page.new_tab(url)
            
            # Wait logic
            is_search_page = any(s in url.lower() for s in ['search', 'bing.com', 'duckduckgo', 'google.com/search', 'searx'])
            if is_search_page:
                # Quick check for results
                result_selectors = ['#results', '#b_results', '#search', '#links', '.result']
                for selector in result_selectors:
                    if tab.ele(selector, timeout=1):
                        break
            else:
                # 1. Wait for document to settle (Fast Dynamic Wait)
                try:
                    tab.wait.doc_loaded(timeout=5)
                    # Brief check for loading overlays (fast skip if none)
                    tab.run_js("""
                        (async () => {
                            const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                            for (let i = 0; i < 15; i++) {
                                const indicators = Array.from(document.querySelectorAll('*')).filter(el => {
                                    try {
                                        const text = (el.textContent || '').toLowerCase();
                                        const id = (el.id || '').toLowerCase();
                                        const cls = (el.getAttribute('class') || '').toLowerCase();
                                        return (text.includes('loading') || id.includes('loading') || cls.includes('loading')) && isVisible(el);
                                    } catch(e) { return false; }
                                });
                                if (indicators.length === 0) break;
                                await new Promise(r => setTimeout(r, 100));
                            }
                        })()
                    """, as_expr=True)
                except: pass

            html = tab.html
            title = tab.title
            final_url = tab.url
            
            raw_screenshot_b64 = None
            if include_screenshot:
                try:
                    # Scrollbar Hiding Best Effort
                    from .manager import SharedBrowserManager
                    SharedBrowserManager.hide_scrollbars(tab)
                    
                    # Inject CSS
                    tab.run_js("""
                        const style = document.createElement('style');
                        style.textContent = `
                            ::-webkit-scrollbar { display: none !important; }
                            html, body { -ms-overflow-style: none !important; scrollbar-width: none !important; }
                        `;
                        document.head.appendChild(style);
                        document.documentElement.style.overflow = 'hidden';
                        document.body.style.overflow = 'hidden';
                    """)
                    
                    raw_screenshot_b64 = tab.get_screenshot(as_base64='jpg', full_page=False)
                except Exception as e:
                    logger.warning(f"ScreenshotService: Failed to capture screenshot: {e}")

            # Extract content
            content = trafilatura.extract(
                html, include_links=True, include_images=True, include_comments=False, 
                include_tables=True, favor_precision=False, output_format="markdown"
            ) or ""

            # 2. Extract Images via Parallelized JS (Gallery)
            images_b64 = []
            try:
                images_b64 = tab.run_js("""
                    (async () => {
                        const blocklist = ['logo', 'icon', 'avatar', 'ad', 'pixel', 'tracker', 'button', 'menu', 'nav'];
                        const candidates = Array.from(document.querySelectorAll('img'));
                        const validCandidates = candidates.filter(img => {
                            if (!img.src || img.src.startsWith('data:')) return false;
                            if (img.naturalWidth < 200 || img.naturalHeight < 150) return false;
                            const alt = (img.alt || '').toLowerCase();
                            const cls = (typeof img.className === 'string' ? img.className : '').toLowerCase();
                            const src = img.src.toLowerCase();
                            if (blocklist.some(b => alt.includes(b) || cls.includes(b) || src.includes(b))) return false;
                            return true;
                        }).slice(0, 10);

                        const fetchImage = async (url) => {
                            try {
                                const controller = new AbortController();
                                const id = setTimeout(() => controller.abort(), 4000);
                                const resp = await fetch(url, { signal: controller.signal });
                                clearTimeout(id);
                                const blob = await resp.blob();
                                return new Promise(resolve => {
                                    const reader = new FileReader();
                                    reader.onloadend = () => resolve(reader.result.split(',')[1]);
                                    reader.onerror = () => resolve(null);
                                    reader.readAsDataURL(blob);
                                });
                            } catch(e) { return null; }
                        };

                        const results = await Promise.all(validCandidates.map(img => fetchImage(img.src)));
                        return results.filter(b64 => !!b64);
                    })()
                """, as_expr=True) or []
                
                if images_b64:
                    logger.info(f"ScreenshotService: Extracted {len(images_b64)} images for {url}")
                
            except Exception as e:
                logger.warning(f"ScreenshotService: Image extraction failed: {e}")

            return {
                "content": content,
                "html": html,
                "title": title,
                "url": final_url,
                "raw_screenshot_b64": raw_screenshot_b64,
                "images": images_b64
            }

        except Exception as e:
            logger.error(f"ScreenshotService: Failed to fetch {url}: {e}")
            return {"content": f"Error: fetch failed ({e})", "title": "Error", "url": url}
        finally:
            if tab:
                try: tab.close()
                except: pass

    async def fetch_pages_batch(self, urls: List[str], timeout: float = 20.0, include_screenshot: bool = True) -> List[Dict[str, Any]]:
        """Fetch multiple pages concurrently."""
        if not urls: return []
        logger.info(f"ScreenshotService: Batch fetching {len(urls)} URLs (screenshots={include_screenshot})")
        tasks = [self.fetch_page(url, timeout, include_screenshot) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def screenshot_url(self, url: str, wait_load: bool = True, timeout: float = 15.0, full_page: bool = False, quality: int = 80) -> Optional[str]:
        """Screenshot URL (Async wrapper for sync)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._screenshot_sync,
            url, wait_load, timeout, full_page, quality
        )

    def _screenshot_sync(self, url: str, wait_load: bool, timeout: float, full_page: bool, quality: int) -> Optional[str]:
        """Synchronous screenshot."""
        if not url: return None
        tab = None
        try:
            self._ensure_ready()
            page = self._manager.page
            if not page: return None
            
            tab = page.new_tab(url)
            try:
                if wait_load:
                    tab.wait.load_complete(timeout=timeout)
                else:
                    tab.wait.doc_loaded(timeout=timeout)
            except: pass
            
            # Wait for main element
            if tab.ele("#main-container"):
                pass 
            
            # Scrollbar Hiding
            from .manager import SharedBrowserManager
            SharedBrowserManager.hide_scrollbars(tab)
            tab.run_js("""
                const style = document.createElement('style');
                style.textContent = `
                    ::-webkit-scrollbar { display: none !important; }
                    html, body { -ms-overflow-style: none !important; scrollbar-width: none !important; }
                `;
                document.head.appendChild(style);
                document.documentElement.style.overflow = 'hidden';
                document.body.style.overflow = 'hidden';
            """)
            
            ele = tab.ele("#main-container")
            if ele:
                return ele.get_screenshot(as_base64='jpg', quality=quality)
            else:
                return tab.get_screenshot(as_base64='jpg', full_page=full_page, quality=quality)
                
        except Exception as e:
            logger.error(f"ScreenshotService: Screenshot URL failed: {e}")
            return None
        finally:
            if tab:
                try: tab.close()
                except: pass

    async def close(self):
        self._executor.shutdown(wait=False)
        logger.info("ScreenshotService: Closed.")

    async def close_async(self):
        await self.close()

# Singleton
_screenshot_service: Optional[ScreenshotService] = None

def get_screenshot_service(headless: bool = True) -> ScreenshotService:
    global _screenshot_service
    if _screenshot_service is None:
        _screenshot_service = ScreenshotService(headless=headless, auto_start=True)
    return _screenshot_service

async def close_screenshot_service():
    global _screenshot_service
    if _screenshot_service:
        await _screenshot_service.close()
        _screenshot_service = None

def prestart_browser(headless: bool = True):
    get_screenshot_service(headless=headless)
