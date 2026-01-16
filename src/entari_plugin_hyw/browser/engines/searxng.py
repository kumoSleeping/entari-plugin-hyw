
import urllib.parse
import re
from typing import List, Dict, Any
from loguru import logger
from .base import SearchEngine

class SearXNGEngine(SearchEngine):
    """
    Parser for DuckDuckGo and SearXNG results.
    Handles both Markdown (from Crawl4AI) and HTML (fallback).
    """
    
    def build_url(self, query: str, limit: int = 10) -> str:
        encoded_query = urllib.parse.quote(query)
        # Default fallback if not configurable per instance, but usually this is what we support as "searxng"
        base = "https://lite.duckduckgo.com/lite/"
        return f"{base}?q={encoded_query}"

    def parse(self, content: str) -> List[Dict[str, Any]]:
        # Prioritize HTML parsing if content looks like HTML
        if "<html" in content.lower() or "<!doctype" in content.lower() or "<div" in content.lower():
            results = self._parse_html(content)
            if results:
                return results

        # Fallback to Markdown
        return self._parse_markdown(content)

    def _parse_html(self, content: str) -> List[Dict[str, Any]]:
        results = []
        seen_urls = set()
        
        # Simple regex for DDG Lite / SearXNG HTML structure
        link_regex = re.compile(r'<a[^>]+href=["\'](http[^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
        
        pos = 0
        while True:
            match = link_regex.search(content, pos)
            if not match:
                break
            
            href = match.group(1)
            title_html = match.group(2)
            
            # Clean title
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            
            pos = match.end()
            
            # Filter junk
            if "search" in href and "q=" in href: continue 
            if "google.com" in href or "bing.com" in href: continue
            if href in seen_urls: continue
            
            # Look ahead for snippet
            snippet_chunk = content[pos:pos+1000]
            snippet_match = re.search(r'(.*?)<a', snippet_chunk, re.DOTALL | re.IGNORECASE)
            raw_snippet = snippet_match.group(1) if snippet_match else snippet_chunk
            
            # Clean HTML tags from snippet
            snippet = re.sub(r'<[^>]+>', ' ', raw_snippet)
            snippet = re.sub(r'\s+', ' ', snippet).strip()
            
            # No truncation as per user request (or very generous limit)
            snippet = snippet[:5000]
            
            # Valid result check
            if title and len(title) > 2 and snippet:
                # Extract images from the result block (rough heuristic)
                images = []
                img_matches = re.findall(r'<img[^>]+src=["\'](http[^"\']+)["\']', snippet_match.group(0) if snippet_match else snippet_chunk)
                for img_url in img_matches:
                    if not any(x in img_url for x in ['favicon', 'icon', 'tracking', 'pixel']):
                         images.append(img_url)
                
                results.append({
                    "title": title,
                    "url": href,
                    "domain": urllib.parse.urlparse(href).hostname or "",
                    "content": snippet,
                    "images": images[:3] # Limit per result
                })
                seen_urls.add(href)
                
        logger.info(f"SearXNG Parser(HTML) found {len(results)} results.")
        return results

    def _parse_markdown(self, content: str) -> List[Dict[str, Any]]:
        results = []
        seen_urls = set()
        
        # Link regex: [Title](URL)
        link_regex = re.compile(r'\[(.*?)\]\((https?://.*?)\)')
        
        lines = content.split('\n')
        current_result = None
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # Check for link
            match = link_regex.search(line)
            if match:
                # Save previous result
                if current_result:
                    results.append(current_result)
                
                title, href = match.groups()
                
                # Filter junk
                if "search" in href and "q=" in href: continue 
                if "google.com" in href or "bing.com" in href: continue 
                if href in seen_urls: 
                    current_result = None
                    continue
                    
                seen_urls.add(href)
                
                current_result = {
                    "title": title,
                    "url": href,
                    "domain": urllib.parse.urlparse(href).hostname or "",
                    "content": "" 
                }
            elif current_result:
                # Append snippet
                if len(current_result["content"]) < 5000:
                    current_result["content"] += " " + line
        
        # Append last
        if current_result:
             results.append(current_result)
        
        logger.info(f"SearXNG Parser(Markdown) found {len(results)} results.")
        return results
