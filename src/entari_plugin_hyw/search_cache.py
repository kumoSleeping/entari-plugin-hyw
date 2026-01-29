"""
Search Result Cache

Caches search results in memory for 10 minutes to support
deep query operations on search results.
"""

import time
import base64
import io
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from loguru import logger


@dataclass
class CacheEntry:
    """A cached search result entry."""
    results: List[Dict[str, Any]]
    query: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ScreenshotCacheEntry:
    """A cached full screenshot."""
    screenshot_b64: str
    url: str
    timestamp: float = field(default_factory=time.time)


class SearchResultCache:
    """
    In-memory cache for search results with TTL-based expiration.

    Cleanup is lazy - performed at the end of each request.
    """

    def __init__(self, ttl_seconds: float = 600.0):  # 10 minutes default
        self._cache: Dict[str, CacheEntry] = {}
        self._screenshot_cache: Dict[str, ScreenshotCacheEntry] = {}  # screenshot_id -> full screenshot
        self._screenshot_counter: int = 0
        self.ttl_seconds = ttl_seconds

    def store(self, message_id: str, results: List[Dict[str, Any]], query: str):
        """
        Store search results associated with a message ID.

        Args:
            message_id: The sent message ID that contains the search results image
            results: List of search result dicts with url, title, content, etc.
            query: The original search query
        """
        self._cache[message_id] = CacheEntry(
            results=results,
            query=query,
            timestamp=time.time()
        )

    def get(self, message_id: str) -> Optional[CacheEntry]:
        """
        Get cached search results for a message ID.

        Returns None if not found or expired.
        """
        entry = self._cache.get(message_id)
        if entry is None:
            return None

        # Check expiration
        if time.time() - entry.timestamp > self.ttl_seconds:
            del self._cache[message_id]
            return None

        return entry

    def store_screenshot(self, screenshot_b64: str, url: str) -> str:
        """
        Store a full screenshot and return its cache ID.

        Args:
            screenshot_b64: Base64 encoded full screenshot
            url: The URL that was screenshotted

        Returns:
            A short cache ID for referencing this screenshot
        """
        self._screenshot_counter += 1
        cache_id = f"ss{self._screenshot_counter:04x}"
        self._screenshot_cache[cache_id] = ScreenshotCacheEntry(
            screenshot_b64=screenshot_b64,
            url=url,
            timestamp=time.time()
        )
        return cache_id

    def get_screenshot(self, cache_id: str) -> Optional[str]:
        """
        Get a cached full screenshot by ID.

        Returns:
            Base64 encoded screenshot or None if not found/expired
        """
        entry = self._screenshot_cache.get(cache_id)
        if entry is None:
            return None

        if time.time() - entry.timestamp > self.ttl_seconds:
            del self._screenshot_cache[cache_id]
            return None

        return entry.screenshot_b64

    def cleanup(self):
        """
        Remove all expired entries.

        Called lazily at the end of each request.
        """
        now = time.time()
        expired_keys = [
            k for k, v in self._cache.items()
            if now - v.timestamp > self.ttl_seconds
        ]
        for k in expired_keys:
            del self._cache[k]

        # Also cleanup screenshot cache
        expired_ss = [
            k for k, v in self._screenshot_cache.items()
            if now - v.timestamp > self.ttl_seconds
        ]
        for k in expired_ss:
            del self._screenshot_cache[k]

    def __len__(self) -> int:
        return len(self._cache)


def crop_to_square_thumbnail(screenshot_b64: str, max_size: int = 400) -> Optional[str]:
    """
    Crop a screenshot to a 1:1 square from the top and resize.

    Args:
        screenshot_b64: Base64 encoded image
        max_size: Maximum dimension of the output square

    Returns:
        Base64 encoded cropped/resized image, or None on error
    """
    try:
        from PIL import Image

        # Decode base64
        img_data = base64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_data))

        width, height = img.size

        # Crop to square from top
        square_size = min(width, height)
        # Crop from top-left, taking full width if width < height
        crop_box = (0, 0, square_size, square_size)
        cropped = img.crop(crop_box)

        # Resize if larger than max_size
        if square_size > max_size:
            cropped = cropped.resize((max_size, max_size), Image.Resampling.LANCZOS)

        # Encode back to base64
        buffer = io.BytesIO()
        cropped.save(buffer, format='JPEG', quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

    except Exception as e:
        logger.warning(f"Failed to crop screenshot: {e}")
        return None


def parse_single_index(text: str) -> Optional[int]:
    """
    Parse a single index from text like "1" or "3".
    
    Args:
        text: The text to parse
        
    Returns:
        0-based index or None if not a valid single index
    """
    if not text:
        return None
    text = text.strip()
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= 10:  # 1-based, max 10 results
            return idx - 1  # Convert to 0-based
    return None


def parse_multi_indices(text: str, max_count: int = 3) -> Optional[List[int]]:
    """
    Parse multiple indices from text like "1-2", "1,2,3", "1、2、5".
    
    Supports:
        - Range: "1-2", "2-4"
        - Comma separated: "1,2,3", "1, 2, 3"
        - Chinese comma: "1、2、5"
        - Space separated: "1 2 3"
        
    Args:
        text: The text to parse
        max_count: Maximum number of indices allowed (default 3), returns None if exceeded
        
    Returns:
        List of 0-based indices, or None if empty/invalid/exceeds max_count
    """
    import re
    
    if not text:
        return None
    
    text = text.strip()
    if not text:
        return None
    
    indices = set()
    
    # Check for range pattern: "1-3"
    range_match = re.match(r'^(\d+)\s*[-–]\s*(\d+)$', text)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if 1 <= start <= 10 and 1 <= end <= 10 and start <= end:
            indices.update(range(start - 1, end))  # 0-based
            if len(indices) > max_count:
                return None  # Exceeds max
            return sorted(indices)
        return None
    
    # Split by comma, Chinese comma, or space
    parts = re.split(r'[,、\s]+', text)
    for part in parts:
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= 10:
                indices.add(idx - 1)  # 0-based
    
    if indices:
        if len(indices) > max_count:
            return None  # Exceeds max
        return sorted(indices)
    return None
