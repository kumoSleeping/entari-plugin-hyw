"""
Browser Service (DrissionPage)

Provides page fetching and screenshot capabilities using DrissionPage.
"""

import asyncio
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List
from loguru import logger
import trafilatura

# Import intelligent completeness checker
from ..crawling.completeness import CompletenessChecker, trigger_lazy_load
from ..crawling.models import CrawlConfig

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

    def _get_tab(self, url: str) -> Any:
        """Create a new tab and navigate to URL."""
        self._ensure_ready()
        return self._manager.new_tab(url)

    def _release_tab(self, tab: Any):
        """Close tab after use."""
        if not tab: return
        try:
            tab.close()
        except:
            pass

    async def search_via_page_input_batch(self, queries: List[str], url: str, selector: str = "#input") -> List[Dict[str, Any]]:
        """
        Execute concurrent searches using page inputs.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._search_via_page_input_batch_sync,
            queries, url, selector
        )

    def _search_via_page_input_batch_sync(self, queries: List[str], url: str, selector: str) -> List[Dict[str, Any]]:
        """Sync batch execution - create tabs sequentially, search in parallel."""
        results = [None] * len(queries)
        tabs = []
        
        # Phase 1: Get/create tabs SEQUENTIALLY (DrissionPage isn't thread-safe for new_tab)
        target_url = url or "https://www.google.com"
        logger.info(f"ScreenshotService: Acquiring {len(queries)} tabs for parallel search...")
        
        for i in range(len(queries)):
            tab = None
            # Try to get from pool first (using shared logic now)
            try:
                tab = self._get_tab(target_url)
            except Exception as e:
                logger.warning(f"ScreenshotService: Batch search tab creation failed: {e}")
            

            
            tabs.append(tab)
        
        logger.info(f"ScreenshotService: {len(tabs)} tabs ready, starting parallel searches...")
        
        # Phase 2: Execute searches in PARALLEL
        def run_search(index, tab, query):
            try:
                logger.debug(f"Search[{index}]: Starting for '{query}' on {tab.url}")
                
                # Wait for page to be ready first
                try:
                    tab.wait.doc_loaded(timeout=10)
                except:
                    pass
                
                # Find input element with wait
                logger.debug(f"Search[{index}]: Looking for input with selector '{selector}'")
                ele = tab.ele(selector, timeout=5)
                if not ele:
                    logger.debug(f"Search[{index}]: Primary selector failed, trying fallbacks")
                    for fallback in ["textarea[name='q']", "#APjFqb", "input[name='q']", "input[type='text']"]:
                        ele = tab.ele(fallback, timeout=2)
                        if ele:
                            logger.debug(f"Search[{index}]: Found input with fallback '{fallback}'")
                            break
                
                if not ele:
                    logger.error(f"Search[{index}]: No input element found on {tab.url}!")
                    results[index] = {"content": "Error: input not found", "title": "Error", "url": tab.url, "html": tab.html[:5000]}
                    return

                logger.debug(f"Search[{index}]: Typing query...")
                ele.input(query)
                
                logger.debug(f"Search[{index}]: Pressing Enter...")
                tab.actions.key_down('enter').key_up('enter')
                
                logger.debug(f"Search[{index}]: Waiting for search results...")
                tab.wait.doc_loaded(timeout=10)
                # Reduced settle wait for extraction
                time.sleep(0.1)
                
                logger.debug(f"Search[{index}]: Extracting content...")
                html = tab.html
                content = trafilatura.extract(
                    html, include_links=True, include_images=True, include_comments=False,
                    include_tables=True, favor_precision=False, output_format="markdown"
                ) or ""
                
                logger.info(f"ScreenshotService: Search '{query}' completed -> {tab.url}")
                
                results[index] = {
                    "content": content,
                    "html": html,
                    "title": tab.title,
                    "url": tab.url,
                    "images": []
                }
                
            except Exception as e:
                logger.error(f"ScreenshotService: Search error for '{query}': {e}")
                results[index] = {"content": f"Error: {e}", "title": "Error", "url": "", "html": ""}
            finally:
                self._release_tab(tab)

        threads = []
        for i, (tab, query) in enumerate(zip(tabs, queries)):
            t = threading.Thread(target=run_search, args=(i, tab, query))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
            
        return results
    
    def _ensure_ready(self):
        """Ensure shared browser is ready."""
        from .manager import get_shared_browser_manager
        self._manager = get_shared_browser_manager(headless=self.headless)

    async def fetch_page(self, url: str, timeout: float = 10.0, include_screenshot: bool = True) -> Dict[str, Any]:
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

    async def search_via_address_bar(self, query: str, timeout: float = 20.0) -> Dict[str, Any]:
        """
        Search using browser's address bar (uses browser's default search engine).
        Simulates: Ctrl+L (focus address bar) -> type query -> Enter
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._search_via_address_bar_sync,
            query,
            timeout
        )
    
    def _search_via_address_bar_sync(self, query: str, timeout: float) -> Dict[str, Any]:
        """Synchronous address bar search logic."""
        if not query:
            return {"content": "Error: missing query", "title": "Error", "url": "", "html": ""}
        
        tab = None
        try:
            self._ensure_ready()
            page = self._manager.page
            if not page:
                return {"content": "Error: Browser not available", "title": "Error", "url": "", "html": ""}
            
            # Open new blank tab
            tab = page.new_tab()
            
            # Focus address bar with Ctrl+L (or Cmd+L on Mac)
            import platform
            if platform.system() == "Darwin":
                tab.actions.key_down('cmd').key_down('l').key_up('l').key_up('cmd')
            else:
                tab.actions.key_down('ctrl').key_down('l').key_up('l').key_up('ctrl')
            
            # Small delay for address bar to focus
            import time as _time
            _time.sleep(0.05)
            
            # Type the query
            tab.actions.type(query)
            
            # Press Enter to search
            tab.actions.key_down('enter').key_up('enter')
            
            # Wait for page to load
            try:
                tab.wait.doc_loaded(timeout=timeout)
                # Reduced wait for initial results
                _time.sleep(0.2)
            except:
                pass
            
            html = tab.html
            title = tab.title
            final_url = tab.url
            
            # Extract content
            content = trafilatura.extract(
                html, include_links=True, include_images=True, include_comments=False,
                include_tables=True, favor_precision=False, output_format="markdown"
            ) or ""
            
            logger.info(f"ScreenshotService: Address bar search completed -> {final_url}")
            
            return {
                "content": content,
                "html": html,
                "title": title,
                "url": final_url,
                "images": []
            }
            
        except Exception as e:
            logger.error(f"ScreenshotService: Address bar search failed: {e}")
            return {"content": f"Error: search failed ({e})", "title": "Error", "url": "", "html": ""}
        finally:
            if tab:
                try: tab.close()
                except: pass

    def _scroll_to_bottom(self, tab, step: int = 800, delay: float = 2.0, timeout: float = 10.0):
        """
        Scroll down gradually to trigger lazy loading.
        
        Args:
            delay: Max wait time per scroll step (seconds) if images aren't loading.
        """
        import time
        start = time.time()
        current_pos = 0
        try:
            while time.time() - start < timeout:
                # Scroll down
                current_pos += step
                tab.run_js(f"window.scrollTo(0, {current_pos});")
                
                # Active Wait: Check if images in viewport are loaded
                # Poll every 100ms, up to 'delay' seconds
                wait_start = time.time()
                while time.time() - wait_start < delay:
                    all_loaded = tab.run_js("""
                        return (async () => {
                            const imgs = Array.from(document.querySelectorAll('img'));
                            const viewportHeight = window.innerHeight;
                            
                            // 1. Identify images currently in viewport
                            const visibleImgs = imgs.filter(img => {
                                const rect = img.getBoundingClientRect();
                                return (rect.top < viewportHeight && rect.bottom > 0) && (rect.width > 0 && rect.height > 0);
                            });
                            
                            if (visibleImgs.length === 0) return true;

                            // 2. Check loading status using decode() AND heuristic for placeholders
                            // Some sites load a tiny blurred placeholder first. 
                            const checks = visibleImgs.map(img => {
                                // Enhanced placeholder detection (matching completeness.py)
                                const dataSrc = img.getAttribute('data-src') || img.getAttribute('data-original') || 
                                                img.getAttribute('data-lazy-src') || img.getAttribute('data-lazy') || '';
                                const className = (typeof img.className === 'string' ? img.className : '').toLowerCase();
                                const loadingAttr = img.getAttribute('loading') || '';
                                const src = img.src || '';
                                
                                const isPlaceholder = (
                                    // data-src not yet loaded
                                    (dataSrc && img.src !== dataSrc) ||
                                    // Natural size much smaller than display (blurred placeholder)
                                    (img.naturalWidth < 50 && img.clientWidth > 100) ||
                                    (img.naturalWidth < 100 && img.clientWidth > 200 && img.naturalWidth * 4 < img.clientWidth) ||
                                    // Lazy-loading class indicators
                                    className.includes('lazy') ||
                                    className.includes('lazyload') ||
                                    className.includes('lozad') ||
                                    // CSS blur filter applied
                                    (window.getComputedStyle(img).filter || '').includes('blur') ||
                                    // loading="lazy" + not complete
                                    (loadingAttr === 'lazy' && !img.complete)
                                );
                                
                                if (isPlaceholder) {
                                    // If it looks like a placeholder, we return false (not loaded)
                                    // unless it stays like this for too long (handled by outer timeout)
                                    return Promise.resolve(false);
                                }

                                if (img.complete && img.naturalHeight > 0) return Promise.resolve(true); 
                                
                                return img.decode().then(() => true).catch(() => false); 
                            });
                            
                            // Race against a small timeout to avoid hanging on one broken image
                            const allDecoded = Promise.all(checks);
                            const timeout = new Promise(resolve => setTimeout(() => resolve(false), 500));
                            
                            // If any check returned false (meaning placeholder or not decoded), result is false
                            return Promise.race([allDecoded, timeout]).then(results => {
                                if (!Array.isArray(results)) return results === true;
                                return results.every(res => res === true);
                            }); 
                        })();
                    """)
                    if all_loaded:
                        break
                    time.sleep(0.1)

                # Check if reached bottom
                height = tab.run_js("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);")
                if current_pos >= height:
                    break
            
            # Ensure final layout settle
            time.sleep(0.2)
            
        except Exception as e:
            logger.warning(f"ScreenshotService: Scroll failed: {e}")

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
            
            # Get from pool
            tab = self._get_tab(url)
            
            # Wait logic - optimized for search pages
            is_search_page = any(s in url.lower() for s in ['search', 'bing.com', 'duckduckgo', 'google.com/search', 'searx'])
            if is_search_page:
                # Optimized waiting: Rapidly poll for ACTUAL results > 0
                start_time = time.time()
                
                # Special fast-path for DDG Lite (HTML only, no JS rendering needed)
                if 'lite.duckduckgo' in url:
                    # just wait for body, it's static HTML
                     try:
                         tab.wait.doc_loaded(timeout=timeout)
                     except: pass
                     # Sleep tiny bit to ensure render
                     time.sleep(0.5)
                else:
                    while time.time() - start_time < timeout:
                        found_results = False
                        try:
                            if 'google' in url.lower():
                                # Check if we have any result items (.g, .MjjYud) or the main container (#search)
                                # Using checks with minimal timeout to allow fast looping
                                if tab.ele('.g', timeout=0.1) or tab.ele('.MjjYud', timeout=0.1) or tab.ele('#search', timeout=0.1):
                                    found_results = True
                            elif 'bing' in url.lower():
                                if tab.ele('.b_algo', timeout=0.1) or tab.ele('#b_results', timeout=0.1):
                                    found_results = True
                            elif 'duckduckgo' in url.lower():
                                if tab.ele('.result', timeout=0.1) or tab.ele('#react-layout', timeout=0.1):
                                    found_results = True
                            else:
                                # Generic fallback: wait for body to be populated
                                if tab.ele('body', timeout=0.1):
                                    found_results = True
                        except:
                            pass
                        
                        if found_results:
                            break
                        time.sleep(0.05)  # Faster polling (50ms) as requested
            else:
                # 1. Wait for document to settle (Fast Dynamic Wait)
                try:
                    tab.wait.doc_loaded(timeout=timeout)
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
            # Strategy: For search pages, use Canvas to grab already loaded images (Instant)
            # For other pages, use fetch (more robust for lazy load)
            images_b64 = []
            try:
                js_code = """
                    (async () => {
                        const blocklist = ['logo', 'icon', 'avatar', 'ad', 'pixel', 'tracker', 'button', 'menu', 'nav'];
                        const candidates = Array.from(document.querySelectorAll('img'));
                        const validImages = [];
                        
                        // Helper: Get base64 from loaded image via Canvas
                        const getBase64 = (img) => {
                            try {
                                const canvas = document.createElement('canvas');
                                canvas.width = img.naturalWidth;
                                canvas.height = img.naturalHeight;
                                const ctx = canvas.getContext('2d');
                                ctx.drawImage(img, 0, 0);
                                return canvas.toDataURL('image/jpeg').split(',')[1];
                            } catch(e) { return null; }
                        };

                        for (const img of candidates) {
                            if (validImages.length >= 8) break;
                            
                            if (img.naturalWidth < 100 || img.naturalHeight < 80) continue;
                            
                            const alt = (img.alt || '').toLowerCase();
                            const cls = (typeof img.className === 'string' ? img.className : '').toLowerCase();
                            const src = (img.src || '').toLowerCase();
                            
                            if (blocklist.some(b => alt.includes(b) || cls.includes(b) || src.includes(b))) continue;
                            
                            // 1. Try Canvas (Instant for loaded images)
                            if (img.complete && img.naturalHeight > 0) {
                                const b64 = getBase64(img);
                                if (b64) {
                                    validImages.push(b64);
                                    continue;
                                }
                            }
                            
                            // 2. Fallback to fetch (only for non-search pages to avoid delay)
                            // We skip fetch for search pages to ensure speed
                            if (!window.location.href.includes('google') && !window.location.href.includes('search')) {
                                try {
                                    const controller = new AbortController();
                                    const id = setTimeout(() => controller.abort(), 2000);
                                    const resp = await fetch(img.src, { signal: controller.signal });
                                    clearTimeout(id);
                                    const blob = await resp.blob();
                                    const b64 = await new Promise(resolve => {
                                        const reader = new FileReader();
                                        reader.onloadend = () => resolve(reader.result.split(',')[1]);
                                        reader.onerror = () => resolve(null);
                                        reader.readAsDataURL(blob);
                                    });
                                    if (b64) validImages.push(b64);
                                } catch(e) {}
                            }
                        }
                        return validImages;
                    })()
                """
                images_b64 = tab.run_js(js_code, as_expr=True) or []
                
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
               self._release_tab(tab)

    async def fetch_pages_batch(self, urls: List[str], timeout: float = 20.0, include_screenshot: bool = True) -> List[Dict[str, Any]]:
        """Fetch multiple pages concurrently."""
        if not urls: return []
        logger.info(f"ScreenshotService: Batch fetching {len(urls)} URLs (screenshots={include_screenshot})")
        tasks = [self.fetch_page(url, timeout, include_screenshot) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def screenshot_urls_batch(self, urls: List[str], timeout: float = 15.0, full_page: bool = True) -> List[Optional[str]]:
        """Take screenshots of multiple URLs concurrently."""
        if not urls: return []
        logger.info(f"ScreenshotService: Batch screenshot {len(urls)} URLs")
        tasks = [self.screenshot_url(url, timeout=timeout, full_page=full_page) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def screenshot_url(self, url: str, wait_load: bool = True, timeout: float = 15.0, full_page: bool = False, quality: int = 80) -> Optional[str]:
        """Screenshot URL (Async wrapper for sync). Returns base64 string only."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._executor,
            self._screenshot_sync,
            url, wait_load, timeout, full_page, quality, False  # extract_content=False
        )
        # Backward compatible: return just the screenshot for old callers
        if isinstance(result, dict):
            return result.get("screenshot_b64")
        return result

    async def screenshot_with_content(self, url: str, timeout: float = 15.0, max_content_length: int = 8000) -> Dict[str, Any]:
        """
        Screenshot URL and extract page content.
        
        Returns:
            Dict with:
                - screenshot_b64: base64 encoded screenshot
                - content: trafilatura extracted text (truncated to max_content_length)
                - title: page title
                - url: final URL
        """
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._executor,
            self._screenshot_sync,
            url, True, timeout, False, 65, True  # quality=65 for balance, extract_content=True
        )
        
        if not isinstance(result, dict):
            return {"screenshot_b64": result, "content": "", "title": "", "url": url}
        
        # Truncate content if needed
        content = result.get("content", "") or ""
        if len(content) > max_content_length:
            content = content[:max_content_length] + "\n\n[内容已截断...]"
        result["content"] = content
        
        return result


    def _screenshot_sync(self, url: str, wait_load: bool, timeout: float, full_page: bool, quality: int, extract_content: bool = False) -> Any:
        """Synchronous screenshot. If extract_content=True, returns Dict else str."""
        if not url: 
            return {"screenshot_b64": None, "content": "", "title": "", "url": url} if extract_content else None
        tab = None
        capture_width = 1440  # Higher resolution for readability
        
        try:
            self._ensure_ready()
            page = self._manager.page
            if not page: return None
            
            # Create blank tab first
            tab = page.new_tab()
            
            # Set viewport BEFORE navigation so page renders at target width from the start
            # This eliminates the need for post-load resize and reflow
            try:
                tab.run_cdp('Emulation.setDeviceMetricsOverride', 
                            width=capture_width, height=900, deviceScaleFactor=1, mobile=False)
            except:
                pass
            
            # Now navigate to the URL - page will render at target width
            tab.get(url)
            
            # Network monitoring removed - not needed for simplified wait logic

            # Initialize crawl config for completeness checking (defined outside try for scope)
            crawl_config = CrawlConfig(
                scan_full_page=True,
                scroll_step=800,
                scroll_delay=0.5,
                scroll_timeout=20.0,  # Increased for lazy-loading pages
                image_load_timeout=3.0,  # Image loading timeout: 3 seconds
                image_stability_checks=3,
            )

            # === Start scrolling immediately to trigger lazy loading ===
            # Scroll first, then wait for DOM - this allows lazy loading to start in parallel
            logger.info(f"ScreenshotService: Starting lazy load scroll for {url} (before DOM wait)")
            trigger_lazy_load(tab, crawl_config)

            # Now wait for DOM to be ready (after scroll has triggered lazy loading)
            try:
                # Wait for full page load (including JS execution)
                try:
                    tab.wait.doc_loaded(timeout=timeout)
                except:
                    pass
                
                # Wait for actual content to appear (for CDN verification pages)
                # Simplified wait logic for screenshot: just check basic readiness
                
                for i in range(20):  # Max 20 iterations (~1s) - much faster
                    try:
                        state = tab.run_js('''
                            return {
                                ready: document.readyState === 'complete',
                                title: document.title,
                                text: document.body.innerText.substring(0, 500) || ""
                            };
                        ''') or {'ready': False, 'title': "", 'text': ""}
                        
                        is_ready = state.get('ready', False)
                        title = state.get('title', "").lower()
                        text_lower = state.get('text', "").lower()
                        text_len = len(text_lower)
                        
                        # Check for verification pages
                        is_verification = "checking your browser" in text_lower or \
                                        "just a moment" in text_lower or \
                                        "please wait" in text_lower or \
                                        "security check" in title or \
                                        "just a moment" in title or \
                                        "loading..." in title
                        
                        # Basic content check
                        has_content = text_len > 100
                        
                        # Pass if ready, not verification, and has content
                        if is_ready and not is_verification and has_content:
                            logger.debug(f"ScreenshotService: Page ready after {i * 0.05:.2f}s")
                            break
                        
                        time.sleep(0.05)
                    except Exception:
                        time.sleep(0.05)
                        continue
                
                # DEBUG: Save HTML to inspect what happened (in data dir)
                try:
                    import os
                    log_path = os.path.join(os.getcwd(), "data", "browser.log.html")
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(f"<!-- URL: {url} -->\n")
                        f.write(tab.html)
                except: pass

            except Exception as e:
                logger.warning(f"ScreenshotService: Page readiness check failed: {e}")
            
            # Scrollbar Hiding first (before any height calculation)
            from .manager import SharedBrowserManager
            SharedBrowserManager.hide_scrollbars(tab)
            
            # Scroll back to top
            tab.run_js("window.scrollTo(0, 0);")
            
            # Image loading monitoring with time tracking - DISABLED
            # No longer waiting for images to load
            # Initialize image tracking JavaScript
            # image_tracking_js = """
            # (() => {
            #     if (!window._imageLoadTracker) {
            #         window._imageLoadTracker = {
            #             startTime: Date.now(),
            #             images: new Map()
            #         };
            #         
            #         const imgs = Array.from(document.querySelectorAll('img'));
            #         const minSize = 50;
            #         
            #         imgs.forEach((img, idx) => {
            #             if (img.clientWidth < minSize && img.clientHeight < minSize) return;
            #             
            #             const src = img.src || img.getAttribute('data-src') || '';
            #             const key = `${idx}_${src.substring(0, 100)}`;
            #             
            #             if (img.complete && img.naturalWidth > 0 && img.naturalHeight > 0) {
            #                 // Already loaded
            #                 window._imageLoadTracker.images.set(key, {
            #                     src: src.substring(0, 150),
            #                     status: 'loaded',
            #                     loadTime: 0,  // Already loaded before tracking
            #                     naturalSize: [img.naturalWidth, img.naturalHeight],
            #                     displaySize: [img.clientWidth, img.clientHeight]
            #                 });
            #             } else {
            #                 // Track loading
            #                 window._imageLoadTracker.images.set(key, {
            #                     src: src.substring(0, 150),
            #                     status: 'pending',
            #                     startTime: Date.now(),
            #                     naturalSize: [img.naturalWidth, img.naturalHeight],
            #                     displaySize: [img.clientWidth, img.clientHeight]
            #                 });
            #                 
            #                 // Add load event listener
            #                 img.addEventListener('load', () => {
            #                     const entry = window._imageLoadTracker.images.get(key);
            #                     if (entry && entry.status === 'pending') {
            #                         entry.status = 'loaded';
            #                         entry.loadTime = Date.now() - entry.startTime;
            #                     }
            #                 });
            #                 
            #                 img.addEventListener('error', () => {
            #                     const entry = window._imageLoadTracker.images.get(key);
            #                     if (entry && entry.status === 'pending') {
            #                         entry.status = 'failed';
            #                         entry.loadTime = Date.now() - entry.startTime;
            #                     }
            #                 });
            #             }
            #         });
            #     }
            #     
            #     // Return current status
            #     const results = [];
            #     window._imageLoadTracker.images.forEach((value, key) => {
            #         const entry = {
            #             src: value.src,
            #             status: value.status,
            #             loadTime: value.status === 'loaded' ? (value.loadTime || 0) : (Date.now() - value.startTime),
            #             naturalSize: value.naturalSize,
            #             displaySize: value.displaySize
            #         };
            #         results.push(entry);
            #     });
            #     
            #     return {
            #         total: results.length,
            #         loaded: results.filter(r => r.status === 'loaded').length,
            #         pending: results.filter(r => r.status === 'pending').length,
            #         failed: results.filter(r => r.status === 'failed').length,
            #         details: results
            #     };
            # })()
            # """
            # 
            # # Initialize tracking
            # tab.run_js(image_tracking_js)
            # 
            # # Monitor image loading with dynamic stop logic
            # check_interval = 0.2  # Check every 200ms
            # image_timeout = 3.0  # Image loading timeout: 3 seconds
            # monitoring_start = time.time()
            # loaded_times = []  # Track load times of completed images
            # 
            # logger.info(f"ScreenshotService: Starting image load monitoring (timeout={image_timeout}s)...")
            # 
            # while True:
            #     elapsed = time.time() - monitoring_start
            #     
            #     # Check timeout first
            #     if elapsed >= image_timeout:
            #         logger.info(f"ScreenshotService: Image loading timeout ({image_timeout}s) reached")
            #         break
            #     
            #     # Get current image status
            #     status = tab.run_js(image_tracking_js, as_expr=True) or {
            #         'total': 0, 'loaded': 0, 'pending': 0, 'failed': 0, 'details': []
            #     }
            #     
            #     # Log each image's status and load time
            #     for img_detail in status.get('details', []):
            #         src_short = img_detail.get('src', '')[:80]
            #         status_str = img_detail.get('status', 'unknown')
            #         load_time = img_detail.get('loadTime', 0)
            #         logger.info(
            #             f"ScreenshotService: Image [{status_str}] "
            #             f"loadTime={load_time:.0f}ms "
            #             f"src={src_short}"
            #         )
            #     
            #     # Collect load times of completed images
            #     loaded_times = [
            #         img.get('loadTime', 0) 
            #         for img in status.get('details', []) 
            #         if img.get('status') == 'loaded' and img.get('loadTime', 0) > 0
            #     ]
            #     
            #     pending_count = status.get('pending', 0)
            #     loaded_count = status.get('loaded', 0)
            #     
            #     # Check stop conditions
            #     if pending_count == 0:
            #         logger.info(f"ScreenshotService: All images loaded. Total: {status.get('total', 0)}, Loaded: {loaded_count}")
            #         break
            #     
            #     # Check dynamic stop condition (if we have loaded images to calculate average)
            #     if loaded_times:
            #         avg_load_time = sum(loaded_times) / len(loaded_times)
            #         max_wait_time = avg_load_time * 2
            #         
            #         # Check if any pending image has exceeded max wait time
            #         pending_images = [
            #             img for img in status.get('details', [])
            #             if img.get('status') == 'pending'
            #         ]
            #         
            #         should_stop = False
            #         for pending_img in pending_images:
            #             wait_time = pending_img.get('loadTime', 0)
            #             if wait_time >= max_wait_time:
            #                 should_stop = True
            #                 logger.info(
            #                     f"ScreenshotService: Stopping - pending image waited {wait_time:.0f}ms, "
            #                     f"exceeds 2x avg load time ({max_wait_time:.0f}ms, avg={avg_load_time:.0f}ms)"
            #                 )
            #                 break
            #         
            #         if should_stop:
            #             break
            #     
            #     # Wait before next check
            #     time.sleep(check_interval)
            
            # Now calculate final height ONCE after all content loaded
            # CompletenessChecker already verified height stability
            try:
                final_height = tab.run_js('''
                    return Math.max(
                        document.body.scrollHeight || 0,
                        document.documentElement.scrollHeight || 0,
                        document.body.offsetHeight || 0,
                        document.documentElement.offsetHeight || 0
                    );
                ''')
                h = min(int(final_height) + 50, 15000)
                tab.run_cdp('Emulation.setDeviceMetricsOverride', 
                            width=capture_width, height=h, deviceScaleFactor=1, mobile=False)
            except:
                pass
            
            # Final scroll to top
            tab.run_js("window.scrollTo(0, 0);")
            
            # Capture screenshot
            screenshot_b64 = tab.get_screenshot(as_base64='jpg', full_page=False)
            
            # Extract content if requested
            if extract_content:
                try:
                    html = tab.html
                    title = tab.title
                    final_url = tab.url
                    
                    # Minimal trafilatura settings to reduce token consumption
                    content = trafilatura.extract(
                        html,
                        include_links=False,     # No links to reduce tokens
                        include_images=False,    # No image descriptions
                        include_comments=False,  # No comments
                        include_tables=False,    # No tables (can be verbose)
                        favor_precision=True,    # Favor precision over recall
                        output_format="txt"      # Plain text (no markdown formatting)
                    ) or ""
                    
                    return {
                        "screenshot_b64": screenshot_b64,
                        "content": content,
                        "title": title,
                        "url": final_url
                    }
                except Exception as e:
                    logger.warning(f"ScreenshotService: Content extraction failed: {e}")
                    return {"screenshot_b64": screenshot_b64, "content": "", "title": "", "url": url}
            
            return screenshot_b64
                
        except Exception as e:
            logger.error(f"ScreenshotService: Screenshot URL failed: {e}")
            return {"screenshot_b64": None, "content": "", "title": "", "url": url} if extract_content else None
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
