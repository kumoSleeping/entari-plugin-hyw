
import urllib.parse
import re
from typing import List, Dict, Any
from loguru import logger
from .base import SearchEngine

class DuckDuckGoEngine(SearchEngine):
    """
    Parser for DuckDuckGo Lite results.
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
        
        # More robust regex: capture ANY href, not just http
        # Matches: <a ... href="..." ...>(...)</a>
        # We capture the full hook to extract title + url
        link_regex = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
        
        pos = 0
        while True:
            match = link_regex.search(content, pos)
            if not match:
                break
            
            raw_href = match.group(1)
            title_html = match.group(2)
            
            # Clean title
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            
            pos = match.end()
            
            # 1. Resolve relative URLs (DDG Lite uses /l/?uddg=...)
            if raw_href.startswith('/'):
                href = "https://lite.duckduckgo.com" + raw_href
            else:
                href = raw_href
                
            # 2. Decode DDG redirect (uddg=...)
            # e.g. /l/?uddg=http%3A%2F%2Fexample.com&rut=...
            if "uddg=" in href:
                try:
                    parsed = urllib.parse.urlparse(href)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if 'uddg' in qs:
                        href = qs['uddg'][0]
                except: pass
            
            # Filter junk
            if not href.startswith("http"): continue
            if "search" in href and "q=" in href: continue 
            if "google.com" in href or "bing.com" in href: continue
            if "duckduckgo.com" in href: continue # Filter self links
            if href in seen_urls: continue
            
            # Improved Snippet Extraction:
            # The structure is consistently:
            # <a ... class="result-link">...</a>
            # ...
            # <td class="result-snippet">...</td>
            
            # Search for the snippet cell specifically associated with this result
            # We search in a reasonable window after the title link
            snippet_window = content[pos:pos+2000]
            snippet_match = re.search(r'class=["\']result-snippet["\'][^>]*>(.*?)</td>', snippet_window, re.IGNORECASE | re.DOTALL)
            
            if snippet_match:
                raw_snippet = snippet_match.group(1)
            else:
                # Fallback to old behavior if structural match fails (e.g. slight layout change)
                # But stop at 'link-text' span or next anchor to avoid junk
                fallback_match = re.search(r'(.*?)(?:<a|<span class=["\']link-text)', snippet_window, re.DOTALL | re.IGNORECASE)
                raw_snippet = fallback_match.group(1) if fallback_match else ""

            # Clean HTML tags
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
                
        logger.info(f"DuckDuckGo Parser(HTML) found {len(results)} results.")
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
        
        logger.info(f"DuckDuckGo Parser(Markdown) found {len(results)} results.")
        return results
