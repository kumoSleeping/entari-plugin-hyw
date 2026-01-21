"""
Search Engines Package

Provides search engine adapters for different search providers.
"""

from .base import SearchEngine
from .google import GoogleEngine
from .duckduckgo import DuckDuckGoEngine
from .default import DefaultEngine

__all__ = [
    "SearchEngine",
    "GoogleEngine", 
    "DuckDuckGoEngine",
    "DefaultEngine",
]
