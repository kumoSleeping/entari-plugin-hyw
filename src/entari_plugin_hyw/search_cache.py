"""
Search Result Cache

Caches search results in memory for 10 minutes to support
deep query operations on search results.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional


@dataclass
class CacheEntry:
    """A cached search result entry."""
    results: List[Dict[str, Any]]
    query: str
    timestamp: float = field(default_factory=time.time)


class SearchResultCache:
    """
    In-memory cache for search results with TTL-based expiration.
    
    Cleanup is lazy - performed at the end of each request.
    """
    
    def __init__(self, ttl_seconds: float = 600.0):  # 10 minutes default
        self._cache: Dict[str, CacheEntry] = {}
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
    
    def __len__(self) -> int:
        return len(self._cache)


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
