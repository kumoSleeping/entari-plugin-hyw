
import urllib.parse
import re
from typing import List, Dict, Any
from loguru import logger
from .base import SearchEngine


class DefaultEngine(SearchEngine):
    """
    Default browser address bar search engine.
    Uses the browser's address bar to search (Ctrl+L -> type -> Enter).
    This uses whatever default search engine the browser is configured with.
    """
    
    # Special marker to indicate this engine uses address bar input
    USE_ADDRESS_BAR = True
    
    def build_url(self, query: str, limit: int = 10) -> str:
        """
        For address bar search, we don't build a URL.
        Return the raw query - SearchService will handle the address bar input.
        """
        # Return a special marker so SearchService knows to use address bar
        return f"__ADDRESS_BAR_SEARCH__:{query}"

    def parse(self, content: str) -> List[Dict[str, Any]]:
        """
        Parse search results from whatever search engine the browser uses.
        We detect the engine from the HTML and use appropriate parsing.
        """
        results = []
        seen_urls = set()
        
        # Detect which search engine based on content
        is_google = 'google' in content.lower() and ('class="g"' in content or 'data-hveid' in content)
        is_bing = 'bing' in content.lower() and 'b_algo' in content
        is_duckduckgo = 'duckduckgo' in content.lower()
        
        if is_google:
            results = self._parse_google(content, seen_urls)
        elif is_bing:
            results = self._parse_bing(content, seen_urls)
        elif is_duckduckgo:
            results = self._parse_duckduckgo(content, seen_urls)
        else:
            # Generic fallback
            results = self._parse_generic(content, seen_urls)
        
        logger.info(f"DefaultEngine parsed {len(results)} results (detected: {'google' if is_google else 'bing' if is_bing else 'ddg' if is_duckduckgo else 'generic'})")
        return results
    
    def _parse_google(self, content: str, seen_urls: set) -> List[Dict[str, Any]]:
        """Parse Google search results."""
        results = []
        # Look for result links
        link_regex = re.compile(
            r'<a[^>]+href="(https?://(?!google\.com|accounts\.google)[^"]+)"[^>]*>([^<]+)</a>',
            re.IGNORECASE
        )
        
        for match in link_regex.finditer(content):
            if len(results) >= 15:
                break
            href = match.group(1)
            title = match.group(2).strip()
            
            if href in seen_urls or not title or len(title) < 3:
                continue
            if any(x in href for x in ['google.com', 'gstatic.com', 'youtube.com/redirect']):
                continue
                
            seen_urls.add(href)
            results.append({
                "title": re.sub(r'<[^>]+>', '', title),
                "url": href,
                "domain": urllib.parse.urlparse(href).hostname or "",
                "content": "",
            })
        return results
    
    def _parse_bing(self, content: str, seen_urls: set) -> List[Dict[str, Any]]:
        """Parse Bing search results."""
        results = []
        link_regex = re.compile(
            r'<a[^>]+href="(https?://(?!bing\.com|microsoft\.com)[^"]+)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL
        )
        
        for match in link_regex.finditer(content):
            if len(results) >= 15:
                break
            href = match.group(1)
            title_html = match.group(2)
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            
            if href in seen_urls or not title or len(title) < 3:
                continue
            if any(x in href for x in ['bing.com', 'microsoft.com', 'msn.com']):
                continue
                
            seen_urls.add(href)
            results.append({
                "title": title,
                "url": href,
                "domain": urllib.parse.urlparse(href).hostname or "",
                "content": "",
            })
        return results
    
    def _parse_duckduckgo(self, content: str, seen_urls: set) -> List[Dict[str, Any]]:
        """Parse DuckDuckGo results."""
        results = []
        link_regex = re.compile(
            r'<a[^>]+href="(https?://(?!duckduckgo\.com)[^"]+)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL
        )
        
        for match in link_regex.finditer(content):
            if len(results) >= 15:
                break
            href = match.group(1)
            title_html = match.group(2)
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            
            if href in seen_urls or not title or len(title) < 3:
                continue
                
            seen_urls.add(href)
            results.append({
                "title": title,
                "url": href,
                "domain": urllib.parse.urlparse(href).hostname or "",
                "content": "",
            })
        return results
    
    def _parse_generic(self, content: str, seen_urls: set) -> List[Dict[str, Any]]:
        """Generic link parser for unknown search engines."""
        results = []
        link_regex = re.compile(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>',
            re.IGNORECASE
        )
        
        for match in link_regex.finditer(content):
            if len(results) >= 15:
                break
            href = match.group(1)
            title = match.group(2).strip()
            
            if href in seen_urls or not title or len(title) < 5:
                continue
            # Skip common non-result URLs
            if any(x in href for x in ['javascript:', 'mailto:', '#', 'login', 'signin', 'account']):
                continue
                
            seen_urls.add(href)
            results.append({
                "title": title,
                "url": href,
                "domain": urllib.parse.urlparse(href).hostname or "",
                "content": "",
            })
        return results

