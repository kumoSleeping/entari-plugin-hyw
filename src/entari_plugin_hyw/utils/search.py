import urllib.parse
from typing import List, Dict, Optional, Any
from loguru import logger
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import CrawlerRunConfig
from crawl4ai.cache_context import CacheMode

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
    Crawl4AI-backed search & fetch service.
    Uses the configured search engine results page (SERP) URL and parses links from the HTML.
    """
    def __init__(self, config: Any):
        self.config = config
        self._default_limit = 8
        self._crawler: Optional[AsyncWebCrawler] = None

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
        Crawl the configured SERP using Crawl4AI and return parsed results.
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
            for ln in lines:
                if url in ln or (domain and domain in ln):
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
        Image search via Crawl4AI media extraction.
        """
        if not query:
            return []

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
