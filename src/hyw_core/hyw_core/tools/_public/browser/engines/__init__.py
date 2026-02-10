"""
Search Engines Package

Provides search engine adapters for different search providers.
"""

from .base import SearchEngine
from .duckduckgo import DuckDuckGoEngine
from .default import DefaultEngine

__all__ = [
    "SearchEngine",
    "DuckDuckGoEngine",
    "DefaultEngine",
]
