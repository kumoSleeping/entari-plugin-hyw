import re
import httpx
import urllib.parse
from typing import List, Dict, Optional, Any
from loguru import logger

class SearchService:
    """
    Specialized service for interacting with SearXNG.
    Uses regex-based HTML parsing to ensure O(n) performance and zero blocking,
    bypasssing heavy DOM parsers like Trafilatura.
    """
    def __init__(self, config: Any):
        self.config = config

    async def search(self, query: str) -> List[Dict[str, str]]:
        """
        Execute search and parse results using Regex.
        Returns a list of dicts: {'title': str, 'url': str, 'content': str}
        """
        # 1. Construct URL (Force HTML format since JSON is 403)
        encoded_query = urllib.parse.quote(query)
        base = getattr(self.config, "search_base_url", "http://127.0.0.1:8888/search?")
        
        # Ensure we don't have double '?' or '&' issues
        sep = "&" if "?" in base else "?"
        
        # Remove any existing format=json if present in base (just in case)
        base = base.replace("format=json&", "").replace("&format=json", "")
        
        # Handle {query} placeholder if present (common in config defaults)
        if "{query}" in base:
            # We need to handle potential other placeholders like {limit} if they exist, or escape them
            # For simplicity, we just replace {query} and ignore format/limit changes since we parse HTML
            # Actually, standard python format() might fail if other braces exist.
            # safe replace:
            url = base.replace("{query}", encoded_query)
            # Remove other common placeholders if they linger
            url = url.replace("{limit}", "8")
        else:
            # Append mode
            url = f"{base}{sep}q={encoded_query}&language=zh-CN"
        
        logger.info(f"SearchService: Fetching {url}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.error(f"Search failed: {resp.status_code}")
                    return []
                html = resp.text
                return self._parse_searxng_html(html)
        except Exception as e:
            logger.error(f"Search execution failed: {e}")
            return []

    def _parse_searxng_html(self, html: str) -> List[Dict[str, str]]:
        """
        Parse SearXNG HTML results using Regex.
        Target structure:
        <article class="result ...">
           <h3><a href="(url)">(title)</a></h3>
           <p class="content">(snippet)</p>
        </article>
        """
        results = []
        
        # Regex to find result blocks. 
        # We split by <article to find chunks, then parse each chunk.
        # This is safer than a global regex which might get confused by nested structures.
        chunks = html.split('<article')
        
        for chunk in chunks[1:]: # Skip preamble
            try:
                # 1. Extract URL and Title
                # Look for <a href="..." ... >Title</a> inside h3
                # Simplified pattern: href="([^"]+)" text is >([^<]+)<
                link_match = re.search(r'href="([^"]+)".*?>([^<]+)<', chunk)
                if not link_match:
                    continue
                
                url = link_match.group(1)
                title = link_match.group(2).strip()
                
                # Verify it's a valid result link (sometimes engine links appear)
                if "searxng" in url or url.startswith("/"):
                    continue

                # 2. Extract Snippet
                # Look for class="content">...<
                # We try to capture text until the next tag open
                snippet_match = re.search(r'class="content"[^>]*>([\s\S]*?)</p>', chunk)
                snippet = ""
                if snippet_match:
                    # Clean up HTML tags from snippet if any remain (basic check)
                    raw_snippet = snippet_match.group(1)
                    snippet = re.sub(r'<[^>]+>', '', raw_snippet).strip()
                
                if url and title:
                    # SAFETY: Truncate snippet to 500 chars to prevent context explosion
                    final_snippet = (snippet or title)[:500]
                    results.append({
                        "title": title,
                        "url": url,
                        "content": final_snippet
                    })
                    
                if len(results) >= 8: # Limit to 8 results
                    break
                    
            except Exception:
                continue
                
        logger.info(f"SearchService: Parsed {len(results)} results")
        return results

    async def image_search(self, query: str) -> List[Dict[str, str]]:
        """
        Perform image search using regex parsing on HTML results.
        """
        if not query: return []
        
        encoded_query = urllib.parse.quote(query)
        base = getattr(self.config, "image_search_base_url", "http://127.0.0.1:8888/search?")
        sep = "&" if "?" in base else "?"
        
        # Clean format=json
        base = base.replace("format=json&", "").replace("&format=json", "")
        
        if "{query}" in base:
            url = base.replace("{query}", encoded_query)
            url = url.replace("{limit}", "8")
        else:
            url = f"{base}{sep}q={encoded_query}&iax=images&ia=images"
        
        logger.info(f"SearchService: Fetching Images {url}")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html_content = resp.text
        except Exception as e:
            logger.error(f"Image Search failed: {e}")
            return []

        # Regex for Images (DuckDuckGo style / Generic)
        # DDG images usually in a script or complex layout. 
        # For simplicity in V2 regex approach, we look for common img tags with logical classes or structure
        # OR, since the user's SearXNG likely returns standard HTML list for images too.
        # SearXNG Image results usually: <img src="..." alt="..."> inside a result container.
        # Let's try a generic pattern for SearXNG image results
        
        results = []
        # SearXNG pattern: <div class="img-search-result"> ... <img src="URL" ...>
        # Or just look for img tags with src that are http
        
        # More robust SearXNG specific regex:
        # Pattern: <img class="image" src="(?P<url>[^"]+)" alt="(?P<title>[^"]+)"
        # This is a guess. Let's try to match standard "result_image" or similar if possible.
        
        # Assuming SearXNG:
        # More robust regex to capture images from various engines (SearXNG, Google, Bing)
        # 1. Try generic <img ... src="..."> with http
        # 2. Try to extract alt text if available
        
        # Pattern 1: Standard img tag with src
        # We look for src="http..." and optional alt
        image_matches = re.finditer(r'<img[^>]+src=["\'](http[^"\']+)["\'][^>]*>', html_content, re.IGNORECASE)
        
        for match in image_matches:
            img_tag = match.group(0)
            img_url = match.group(1)
            
            # Extract alt/title
            alt_match = re.search(r'alt=["\']([^"\']*)["\']', img_tag, re.IGNORECASE)
            title = alt_match.group(1) if alt_match else ""
            
            # Filter out tiny icons/favicons/data uris if possible
            if "favicon" in img_url or "static" in img_url or "data:image" in img_url:
                continue
                
            results.append({
                "title": title or "Image",
                "url": img_url,
                "content": f"Image: {title}"
            })
            
            if len(results) >= 8:
                break
                
        return results
