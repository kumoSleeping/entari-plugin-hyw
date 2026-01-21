"""
Image Caching Module for Pre-downloading Images

This module provides async image pre-download functionality to reduce render time.
Images are downloaded in the background when search results are obtained,
and cached as base64 data URLs for instant use during rendering.
"""

import asyncio
import base64
import hashlib
from typing import Dict, List, Optional, Any
from loguru import logger
import httpx



class ImageCache:
    """
    Async image cache that pre-downloads images as base64.
    
    Usage:
        cache = ImageCache()
        
        # Start pre-downloading images (non-blocking)
        cache.start_prefetch(image_urls)
        
        # Later, get cached image (blocking if not ready)
        cached_url = await cache.get_cached(url)  # Returns data:image/... or original URL
    """
    
    def __init__(
        self, 
        max_size_kb: int = 500,  # Max image size to cache (KB)
        max_concurrent: int = 6,  # Max concurrent downloads
    ):
        self.max_size_bytes = max_size_kb * 1024
        self.max_concurrent = max_concurrent
        
        # Cache storage: url -> base64_data_url or None (if failed)
        self._cache: Dict[str, Optional[str]] = {}
        # Pending downloads: url -> asyncio.Task
        self._pending: Dict[str, asyncio.Task] = {}
        # Semaphore for concurrent downloads
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # Lock for cache access
        self._lock = asyncio.Lock()
    
    def start_prefetch(self, urls: List[str]) -> None:
        """
        Start pre-downloading images in the background (non-blocking).
        
        Args:
            urls: List of image URLs to prefetch
        """
        if not httpx:
            logger.warning("ImageCache: httpx not installed, prefetch disabled")
            return
            
        for url in urls:
            if not url or not url.startswith("http"):
                continue
            if url in self._cache or url in self._pending:
                continue
            
            # Create background task
            task = asyncio.create_task(self._download_image(url))
            self._pending[url] = task
    
    async def _download_image(self, url: str) -> Optional[str]:
        """
        Download a single image and convert to base64.
        
        Returns:
            Base64 data URL or None if failed/too large
        """
        async with self._semaphore:
            try:
                # No timeout - images download until agent ends
                async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                    })
                    resp.raise_for_status()
                    
                    # Check content length
                    content_length = resp.headers.get("content-length")
                    if content_length and int(content_length) > self.max_size_bytes:
                        logger.debug(f"ImageCache: Skipping {url} (too large: {content_length} bytes)")
                        async with self._lock:
                            self._cache[url] = None
                            self._pending.pop(url, None)
                        return None
                    
                    # Read content
                    content = resp.content
                    if len(content) > self.max_size_bytes:
                        logger.debug(f"ImageCache: Skipping {url} (content too large: {len(content)} bytes)")
                        async with self._lock:
                            self._cache[url] = None
                            self._pending.pop(url, None)
                        return None
                    
                    # Determine MIME type
                    content_type = resp.headers.get("content-type", "").lower()
                    if "jpeg" in content_type or "jpg" in content_type:
                        mime = "image/jpeg"
                    elif "png" in content_type:
                        mime = "image/png"
                    elif "gif" in content_type:
                        mime = "image/gif"
                    elif "webp" in content_type:
                        mime = "image/webp"
                    elif "svg" in content_type:
                        mime = "image/svg+xml"
                    else:
                        # Try to infer from URL
                        url_lower = url.lower()
                        if ".jpg" in url_lower or ".jpeg" in url_lower:
                            mime = "image/jpeg"
                        elif ".png" in url_lower:
                            mime = "image/png"
                        elif ".gif" in url_lower:
                            mime = "image/gif"
                        elif ".webp" in url_lower:
                            mime = "image/webp"
                        elif ".svg" in url_lower:
                            mime = "image/svg+xml"
                        else:
                            mime = "image/jpeg"  # Default fallback
                    
                    # Encode to base64
                    b64 = base64.b64encode(content).decode("utf-8")
                    data_url = f"data:{mime};base64,{b64}"
                    
                    async with self._lock:
                        self._cache[url] = data_url
                        self._pending.pop(url, None)
                    
                    logger.debug(f"ImageCache: Cached {url} ({len(content)} bytes)")
                    return data_url
                    
            except Exception as e:
                logger.debug(f"ImageCache: Failed to download {url}: {e}")
            
            async with self._lock:
                self._cache[url] = None
                self._pending.pop(url, None)
            return None
    
    async def get_cached(self, url: str, wait: bool = True) -> str:
        """
        Get cached image data URL, or original URL if not cached.
        
        Args:
            url: Original image URL
            wait: If True, wait for pending download to complete (no timeout - waits until agent ends)
            
        Returns:
            Cached data URL or original URL
        """
        if not url:
            return url
            
        # Check if already cached
        async with self._lock:
            if url in self._cache:
                cached = self._cache[url]
                return cached if cached else url  # Return original if cached as None (failed)
            
            pending_task = self._pending.get(url)
        
        # Wait for pending download if requested (no timeout - waits until cancelled)
        if pending_task and wait:
            try:
                await pending_task
                async with self._lock:
                    cached = self._cache.get(url)
                    return cached if cached else url
            except asyncio.CancelledError:
                logger.debug(f"ImageCache: Download cancelled for {url}")
                return url
            except Exception:
                return url
        
        return url
    
    async def get_all_cached(self, urls: List[str]) -> Dict[str, str]:
        """
        Get cached URLs for multiple images.
        
        Args:
            urls: List of original URLs
            
        Returns:
            Dict mapping original URL to cached data URL (or original if not cached)
        """
        result = {}
        
        # Wait for all pending downloads first (no timeout - waits until cancelled)
        pending_tasks = []
        async with self._lock:
            for url in urls:
                if url in self._pending:
                    pending_tasks.append(self._pending[url])
        
        if pending_tasks:
            try:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                logger.debug(f"ImageCache: Batch download cancelled")
        
        # Collect results
        for url in urls:
            async with self._lock:
                cached = self._cache.get(url)
            result[url] = cached if cached else url
            
        return result
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        cached_count = sum(1 for v in self._cache.values() if v is not None)
        failed_count = sum(1 for v in self._cache.values() if v is None)
        return {
            "cached": cached_count,
            "failed": failed_count,
            "pending": len(self._pending),
            "total": len(self._cache) + len(self._pending),
        }
    
    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()


# Global cache instance for reuse across requests
_global_cache: Optional[ImageCache] = None


def get_image_cache() -> ImageCache:
    """Get or create the global image cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = ImageCache()
    return _global_cache


async def prefetch_images(urls: List[str]) -> None:
    """
    Convenience function to start prefetching images.
    
    Args:
        urls: List of image URLs to prefetch
    """
    cache = get_image_cache()
    cache.start_prefetch(urls)


async def get_cached_images(urls: List[str]) -> Dict[str, str]:
    """
    Convenience function to get cached images.
    
    Args:
        urls: List of original URLs
        
    Returns:
        Dict mapping original URL to cached data URL
    """
    cache = get_image_cache()
    return await cache.get_all_cached(urls)
