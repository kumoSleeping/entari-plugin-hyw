import urllib.parse
import asyncio
import re
import html
from typing import List, Dict, Optional, Any
from loguru import logger
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import CrawlerRunConfig
from crawl4ai.cache_context import CacheMode

# Optional imports for new strategies
try:
    import httpx
except ImportError:
    httpx = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

# Shared crawler instance to avoid repeated init
_shared_crawler: Optional[AsyncWebCrawler] = None


async def get_shared_crawler() -> AsyncWebCrawler:
    global _shared_crawler
    if _shared_crawler is None:
        _shared_crawler = AsyncWebCrawler()
        await _shared_crawler.start()
    return _shared_crawler


async def close_shared_crawler():
    global _shared_crawler
    if _shared_crawler:
        try:
            await _shared_crawler.close()
        except Exception:
            pass
        _shared_crawler = None

class SearchService:
    """
    Multi-strategy search & fetch service.
    Supported providers: 'crawl4ai' (default), 'httpx', 'ddgs'.
    """
    def __init__(self, config: Any):
        self.config = config
        self._default_limit = getattr(config, "search_limit", 8)
        self._crawler: Optional[AsyncWebCrawler] = None
        
        # Configuration for retries/timeouts
        self._search_timeout = getattr(config, "search_timeout", 10.0)
        self._search_retries = getattr(config, "search_retries", 2)
        self._provider = getattr(config, "search_provider", "crawl4ai")
        logger.info(f"SearchService initialized: provider='{self._provider}', limit={self._default_limit}, timeout={self._search_timeout}s")

    def _build_search_url(self, query: str) -> str:
        encoded_query = urllib.parse.quote(query)
        base = getattr(self.config, "search_base_url", "https://lite.duckduckgo.com/lite/?q={query}")
        if "{query}" in base:
            return base.replace("{query}", encoded_query).replace("{limit}", str(self._default_limit))
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}q={encoded_query}"

    def _build_image_url(self, query: str) -> str:
        encoded_query = urllib.parse.quote(query)
        base = getattr(self.config, "image_search_base_url", "https://duckduckgo.com/?q={query}&iax=images&ia=images")
        if "{query}" in base:
            return base.replace("{query}", encoded_query).replace("{limit}", str(self._default_limit))
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}q={encoded_query}&iax=images&ia=images"

    async def search(self, query: str) -> List[Dict[str, str]]:
        """
        Dispatch search to the configured provider.
        """
        if not query:
            return []

        provider = self._provider.lower()
        logger.info(f"SearchService: searching for '{query}' using provider='{provider}'")

        if provider == "httpx":
            return await self._search_httpx(query)
        elif provider == "ddgs":
            return await self._search_ddgs(query)
        else:
            # Default to crawl4ai for backward compatibility or explicit choice
            return await self._search_crawl4ai(query)

    async def _search_httpx(self, query: str) -> List[Dict[str, str]]:
        """
        Directly fetch https://lite.duckduckgo.com/lite/ via httpx and parse HTML.
        Fast, no browser overhead.
        """
        if not httpx:
            logger.error("SearchService: httpx not installed, fallback to crawl4ai")
            return await self._search_crawl4ai(query)

        url = self._build_search_url(query)
        
        results: List[Dict[str, str]] = []
        try:
            async with httpx.AsyncClient(timeout=self._search_timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
                })
                resp.raise_for_status()
                html_content = resp.text

            # Regex parsing for DDG Lite
            snippet_regex = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
            link_regex = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
            
            raw_links = link_regex.findall(html_content)
            
            seen = set()
            for href, text in raw_links:
                if len(results) >= self._default_limit:
                    break
                    
                # Clean href
                if "duckduckgo.com" in href: 
                    if "uddg=" in href:
                         parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                         href = parsed.get("uddg", [href])[0]
                    else:
                        continue
                        
                if not href.startswith("http"):
                    continue
                    
                if href in seen:
                    continue
                seen.add(href)
                
                # Title clean
                title = re.sub(r'<[^>]+>', '', text).strip()
                title = html.unescape(title)
                
                results.append({
                    "title": title,
                    "url": href,
                    "domain": urllib.parse.urlparse(href).hostname or "",
                    "content": title 
                })

            if not results:
                 logger.warning("SearchService(httpx): No results parsed via regex.")
                 
            return results

        except Exception as e:
            logger.error(f"SearchService(httpx) failed: {e}")
            return []

    async def _search_ddgs(self, query: str) -> List[Dict[str, str]]:
        """
        Use duckduckgo_search library (Sync DDGS).
        Executes in thread pool to allow async usage.
        Supports retries and timeouts.
        """
        if not DDGS:
            logger.error("SearchService: duckduckgo_search not installed, fallback to crawl4ai")
            return await self._search_crawl4ai(query)

        def _do_sync_search():
            """Sync search function to run in thread"""
            results: List[Dict[str, str]] = []
            final_exc = None
            
            for attempt in range(self._search_retries + 1):
                try:
                    with DDGS(timeout=self._search_timeout) as ddgs:
                         # Use positional argument for query to be safe across versions
                        ddgs_gen = ddgs.text(
                            query,
                            region='cn-zh',
                            safesearch='moderate',
                            max_results=self._default_limit,
                            backend="duckduckgo",
                        )
                        
                        if ddgs_gen:
                            for r in ddgs_gen:
                                results.append({
                                    "title": r.get("title", ""),
                                    "url": r.get("href", ""),
                                    "domain": urllib.parse.urlparse(r.get("href", "")).hostname or "",
                                    "content": r.get("body", "")
                                })
                                if len(results) >= self._default_limit:
                                    break
                    
                    return results, None

                except Exception as e:
                    final_exc = e
                    if attempt < self._search_retries:
                         import time
                         time.sleep(1)

            return [], final_exc

        # Run sync search in executor
        try:
            results, err = await asyncio.to_thread(_do_sync_search)
            
            if err:
                 logger.warning(f"SearchService(ddgs) text search failed after retries: {err}")
                 return []
            
            logger.info(f"SearchService(ddgs): Got {len(results)} text results")
            return results

        except Exception as e:
            logger.error(f"SearchService(ddgs) thread execution failed: {e}")
            return []

    async def _search_ddgs_images(self, query: str) -> List[Dict[str, str]]:
        """
        Use duckduckgo_search library for images.
        """
        if not DDGS:
            return []

        def _do_sync_image_search():
            results: List[Dict[str, str]] = []
            final_exc = None
            
            for attempt in range(self._search_retries + 1):
                try:
                    with DDGS(timeout=self._search_timeout) as ddgs:
                        ddgs_gen = ddgs.images(
                            query,
                            region='cn-zh',
                            safesearch='moderate',
                            max_results=self._default_limit,
                        )
                        
                        if ddgs_gen:
                            for r in ddgs_gen:
                                # DDGS images returns: title, image, thumbnail, url, source, etc.
                                # API might differ, adapt to standard format
                                results.append({
                                    "title": r.get("title", "Image"),
                                    "url": r.get("image", "") or r.get("url", ""), # Full image URL
                                    "thumbnail": r.get("thumbnail", ""),
                                    "domain": r.get("source", "") or urllib.parse.urlparse(r.get("url", "")).hostname or "",
                                })
                                if len(results) >= self._default_limit:
                                    break
                    
                    return results, None
                except Exception as e:
                    final_exc = e
                    if attempt < self._search_retries:
                         import time
                         time.sleep(1)

            return [], final_exc

        try:
            results, err = await asyncio.to_thread(_do_sync_image_search)
            if err:
                 logger.warning(f"SearchService(ddgs) image search failed: {err}")
                 return []
            
            logger.info(f"SearchService(ddgs): Got {len(results)} image results")
            return results
        except Exception as e:
             logger.error(f"SearchService(ddgs) image thread failed: {e}")
             return []

    async def _search_crawl4ai(self, query: str) -> List[Dict[str, str]]:
        """
        Crawl the configured SERP using Crawl4AI and return parsed results.
        Original implementation.
        """
        if not query:
            return []

        url = self._build_search_url(query)
        logger.info(f"SearchService(Crawl4AI): fetching {url}")

        try:
            crawler = await self._get_crawler()
            result = await crawler.arun(
                url=url,
                config=CrawlerRunConfig(
                    wait_until="domcontentloaded",
                    wait_for="article",
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    screenshot=False,
                    capture_console_messages=False,
                    capture_network_requests=False,
                ),
            )
            return self._parse_markdown_result(result, limit=self._default_limit)
        except Exception as e:
            logger.error(f"Crawl4AI search failed: {e}")
            return []

    def _parse_markdown_result(self, result, limit: int = 8) -> List[Dict[str, str]]:
        """Parse Crawl4AI result into search items without manual HTML parsing."""
        md = (result.markdown or result.extracted_content or "").strip()
        lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
        links = result.links.get("external", []) if getattr(result, "links", None) else []
        seen = set()
        results: List[Dict[str, str]] = []

        def find_snippet(url: str, domain: str) -> str:
            for i, ln in enumerate(lines):
                # Avoid matching DDG internal links (filter by site, etc) which contain the target URL in query params
                if "duckduckgo.com" in ln:
                    continue
                
                if url in ln or (domain and domain in ln):
                    # If this line looks like the title/link line (e.g. starts with [Title](url) or similar), 
                    # and the NEXT line exists, return the next line as snippet.
                    if i + 1 < len(lines) and len(ln) < 300: # heuristic: title lines are usually shorter
                         return lines[i+1][:400]
                    return ln[:400]
            # fallback to first non-empty line
            return lines[0][:400] if lines else ""

        for link in links:
            url = link.get("href") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            domain = urllib.parse.urlparse(url).hostname or ""
            title = link.get("title") or link.get("text") or url
            snippet = find_snippet(url, domain)
            results.append({
                "title": title.strip(),
                "url": url,
                "domain": domain,
                "content": snippet or title,
            })
            if len(results) >= limit:
                break

        if not results:
            logger.warning(f"SearchService: no results parsed; md_length={len(md)}, links={len(links)}")
        else:
            logger.info(f"SearchService: parsed {len(results)} results via Crawl4AI links")
        return results

    async def fetch_page(self, url: str) -> Dict[str, str]:
        """
        Fetch a single page via Crawl4AI and return cleaned markdown/text plus metadata.
        """
        if not url:
            return {"content": "Error: missing url", "title": "Error", "url": ""}

        try:
            crawler = await self._get_crawler()
            result = await crawler.arun(
                url=url,
                config=CrawlerRunConfig(
                    wait_until="networkidle",
                    wait_for_images=False,  # Faster: skip image loading
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    screenshot=False,
                    capture_console_messages=False,
                    capture_network_requests=False,
                ),
            )
            if not result.success:
                return {"content": f"Error: crawl failed ({result.error_message or 'unknown'})", "title": "Error", "url": url}
            
            content = result.markdown or result.extracted_content or result.cleaned_html or result.html or ""
            # Extract metadata if available, otherwise fallback
            title = "No Title"
            if result.metadata:
                title = result.metadata.get("title") or result.metadata.get("og:title") or title
            
            # If metadata title is missing/generic, try to grab from links or url? No, metadata is best.
            if title == "No Title" and result.links:
                 # Minimal fallback not really possible without parsing HTML again or regex
                 pass

            return {
                "content": content[:8000], 
                "title": title, 
                "url": result.url or url 
            }
        except Exception as e:
            logger.error(f"Crawl4AI fetch failed: {e}")
            return {"content": f"Error: crawl failed ({e})", "title": "Error", "url": url}

    async def _get_crawler(self) -> AsyncWebCrawler:
        # Prefer shared crawler to minimize INIT logs; fall back to local if needed
        try:
            return await get_shared_crawler()
        except Exception as e:
            logger.warning(f"Shared crawler unavailable, creating local: {e}")
            if self._crawler is None:
                self._crawler = AsyncWebCrawler()
                await self._crawler.start()
            return self._crawler

    async def close(self):
        if self._crawler:
            try:
                await self._crawler.close()
            except Exception:
                pass
            self._crawler = None

    async def image_search(self, query: str) -> List[Dict[str, str]]:
        """
        Image search via Crawl4AI media extraction or DDGS.
        """
        if not query:
            return []

        # If ddgs is selected, use it
        if self._provider == "ddgs":
             return await self._search_ddgs_images(query)

        url = self._build_image_url(query)
        logger.info(f"SearchService(Crawl4AI Image): fetching {url}")

        try:
            # Use image crawler (text_mode=False) for image search
            crawler = await self._get_crawler()
            result = await crawler.arun(
                url=url,
                config=CrawlerRunConfig(
                    wait_until="networkidle",
                    wait_for_images=True,
                    wait_for="img",
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    screenshot=False,
                    capture_console_messages=False,
                    capture_network_requests=False,
                ),
            )
            images = []
            seen = set()
            for img in result.media.get("images", []):
                src = img.get("src") or ""
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                if not src.startswith("http"):
                    continue
                if src in seen:
                    continue
                seen.add(src)
                alt = (img.get("alt") or img.get("desc") or "").strip()
                domain = urllib.parse.urlparse(src).hostname or ""
                images.append({
                    "title": alt or "Image",
                    "url": src,
                    "domain": domain,
                    "content": alt or "Image",
                })
                if len(images) >= self._default_limit:
                    break
            if not images:
                logger.warning(f"SearchService: no images parsed; media_count={len(result.media.get('images', []))}")
            else:
                logger.info(f"SearchService: parsed {len(images)} images via Crawl4AI media")
            return images
        except Exception as e:
            logger.error(f"Crawl4AI image search failed: {e}")
            return []
