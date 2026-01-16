
import urllib.parse
import re
from typing import List, Dict, Any
from loguru import logger
from .base import SearchEngine

class BingEngine(SearchEngine):
    """
    Search engine implementation for Bing.
    """
    
    def build_url(self, query: str, limit: int = 10) -> str:
        encoded_query = urllib.parse.quote(query)
        base = "https://www.bing.com/search"
        return f"{base}?form=&q={encoded_query}"

    def parse(self, content: str) -> List[Dict[str, Any]]:
        results = []
        # Split by b_algo to isolate results
        chunks = content.split('class="b_algo"')
        
        # Helper to decode Bing URLs roughly
        def decode_bing_url(u):
            if "bing.com/ck/a?" not in u: return u
            try:
                # Url is usually like ...&u=a1<base64>&...
                # We look for &u=...
                import base64
                match = re.search(r'[?&]u=a1([^&]+)', u)
                if match:
                    # Bing uses a modified base64 (url safe) and adds 'a1' prefix
                    # We stripped 'a1' in regex match group
                    b64 = match.group(1)
                    # padding
                    b64 += '=' * (-len(b64) % 4)
                    # url safe
                    b64 = b64.replace('-', '+').replace('_', '/')
                    decoded = base64.b64decode(b64).decode('utf-8')
                    return decoded
            except Exception:
                pass
            return u

        seen_urls = set()

        for chunk in chunks[1:]:
            # Exact regexes for title and snippet within the chunk
            # Title: <h2><a href="...">...</a></h2>
            link_match = re.search(r'<h2[^>]*>.*?<a[^>]+href=["\'](http[^"\']+)["\'][^>]*>(.*?)</a>', chunk, re.IGNORECASE | re.DOTALL)
            if not link_match:
                # Fallback: pure a tag
                link_match = re.search(r'<a[^>]+href=["\'](http[^"\']+)["\'][^>]*>(.*?)</a>', chunk, re.IGNORECASE | re.DOTALL)
            
            if link_match:
                raw_url = link_match.group(1)
                title_html = link_match.group(2)
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                
                url = decode_bing_url(raw_url)
                
                if url in seen_urls: continue
                seen_urls.add(url)

                # Snippet: class="b_caption" ... <p> ... </p> or just div text
                snippet = ""
                caption_match = re.search(r'class="b_caption"[^>]*>(.*?)</div>', chunk, re.IGNORECASE | re.DOTALL)
                if caption_match:
                    snippet_html = caption_match.group(1)
                    snippet = re.sub(r'<[^>]+>', ' ', snippet_html).strip()
                else:
                    # Fallback snippet
                    start = link_match.end()
                    snippet = re.sub(r'<[^>]+>', ' ', chunk[start:start+600]).strip()
                
                snippet = re.sub(r'\s+', ' ', snippet).strip()
                
                # Image extraction (basic)
                images = []
                img_matches = re.findall(r'<img[^>]+src=["\'](http[^"\']+)["\']', chunk)
                for img_url in img_matches:
                    if not any(x in img_url for x in ['favicon', 'icon', 'tracking', 'pixel']):
                         images.append(img_url)

                if url and title:
                    results.append({
                        "title": title,
                        "url": url,
                        "domain": urllib.parse.urlparse(url).hostname or "",
                        "content": snippet[:5000],
                        "images": images[:3]
                    })

        logger.info(f"BingEngine parsed {len(results)} results.")
        return results
