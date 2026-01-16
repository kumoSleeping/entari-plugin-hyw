
import urllib.parse
import re
from typing import List, Dict, Any
from loguru import logger
from .base import SearchEngine


class GoogleEngine(SearchEngine):
    """
    Search engine implementation for Google.
    Parses Google Search HTML results.
    """
    
    def build_url(self, query: str, limit: int = 10) -> str:
        encoded_query = urllib.parse.quote(query)
        return f"https://www.google.com/search?q={encoded_query}"

    def parse(self, content: str) -> List[Dict[str, Any]]:
        results = []
        seen_urls = set()
        
        # Google search results are in blocks with class="MjjYud" or similar containers
        # Split by result blocks first for more accurate extraction
        
        # Method 1: Split by common result block classes
        block_patterns = [
            r'<div class="MjjYud"[^>]*>',
            r'<div class="tF2Cxc"[^>]*>',
            r'<div class="g Ww4FFb"[^>]*>',
        ]
        
        blocks = [content]
        for bp in block_patterns:
            new_blocks = []
            for block in blocks:
                parts = re.split(bp, block)
                new_blocks.extend(parts)
            blocks = new_blocks
        
        for block in blocks:
            if len(block) < 100:
                continue
                
            # Find URL in this block - prefer links with h3 nearby
            url_match = re.search(r'<a[^>]+href="(https?://(?!www\.google\.|google\.|webcache\.googleusercontent\.)[^"]+)"[^>]*>', block)
            if not url_match:
                continue
                
            url = url_match.group(1)
            if url in seen_urls or self._should_skip_url(url):
                continue
            
            # Find h3 title in this block
            h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.IGNORECASE | re.DOTALL)
            if not h3_match:
                continue
                
            title = re.sub(r'<[^>]+>', '', h3_match.group(1)).strip()
            if not title or len(title) < 2:
                continue
            
            seen_urls.add(url)
            
            # Extract snippet from VwiC3b class (Google's snippet container)
            snippet = ""
            snippet_match = re.search(r'<div[^>]*class="[^"]*VwiC3b[^"]*"[^>]*>(.*?)</div>', block, re.IGNORECASE | re.DOTALL)
            if snippet_match:
                snippet = re.sub(r'<[^>]+>', ' ', snippet_match.group(1)).strip()
                snippet = re.sub(r'\s+', ' ', snippet).strip()
            
            # Fallback: look for any text after h3
            if not snippet:
                # Try other common snippet patterns
                alt_patterns = [
                    r'<span[^>]*class="[^"]*aCOpRe[^"]*"[^>]*>(.*?)</span>',
                    r'<div[^>]*data-snc[^>]*>(.*?)</div>',
                ]
                for ap in alt_patterns:
                    am = re.search(ap, block, re.IGNORECASE | re.DOTALL)
                    if am:
                        snippet = re.sub(r'<[^>]+>', ' ', am.group(1)).strip()
                        snippet = re.sub(r'\s+', ' ', snippet).strip()
                        break
            
            # Extract images from this block
            images = []
            # Pattern 1: Regular img src (excluding data: and tracking pixels)
            # Note: gstatic.com/images/branding is logo, but encrypted-tbn*.gstatic.com are thumbnails
            img_matches = re.findall(r'<img[^>]+src="(https?://[^"]+)"', block)
            for img_url in img_matches:
                # Decode HTML entities
                img_url = img_url.replace('&amp;', '&')
                # Skip tracking/icon/small images (but allow encrypted-tbn which are valid thumbnails)
                if any(x in img_url.lower() for x in ['favicon', 'icon', 'tracking', 'pixel', 'logo', 'gstatic.com/images/branding', '1x1', 'transparent', 'gstatic.com/images/icons']):
                    continue
                if img_url not in images:
                    images.append(img_url)
            
            # Pattern 2: data-src (lazy loaded images)
            data_src_matches = re.findall(r'data-src="(https?://[^"]+)"', block)
            for img_url in data_src_matches:
                img_url = img_url.replace('&amp;', '&')
                if any(x in img_url.lower() for x in ['favicon', 'icon', 'tracking', 'pixel', 'logo']):
                    continue
                if img_url not in images:
                    images.append(img_url)
            
            results.append({
                "title": title,
                "url": url,
                "domain": urllib.parse.urlparse(url).hostname or "",
                "content": snippet[:1000],
                "images": images[:3]  # Limit to 3 images per result
            })
            
            if len(results) >= 15:
                break
        
        total_images = sum(len(r.get("images", [])) for r in results)
        logger.info(f"GoogleEngine parsed {len(results)} results with {total_images} images total.")
        return results
    
    def _should_skip_url(self, url: str) -> bool:
        """Check if URL should be skipped."""
        skip_patterns = [
            "google.com",
            "googleusercontent.com",
            "gstatic.com",
            "youtube.com/watch",  # Keep channel/playlist but skip individual videos
            "maps.google",
            "translate.google",
            "accounts.google",
            "support.google",
            "policies.google",
            "schema.org",
            "javascript:",
            "data:",
            "#",
        ]
        
        for pattern in skip_patterns:
            if pattern in url.lower():
                return True
        
        # Skip very short URLs (likely invalid)
        if len(url) < 20:
            return True
        
        # Skip URLs that are just root domains without path
        parsed = urllib.parse.urlparse(url)
        if not parsed.path or parsed.path == "/":
            return True
            
        return False
