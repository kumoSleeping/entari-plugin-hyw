from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class HywCoreConfig:
    """Core configuration for HywAgent"""

    # LLM Settings
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "gpt-4o"
    temperature: float = 0.7

    # Browser Settings
    headless: bool = True

    # Behavior
    language: str = "Simplified Chinese"
    theme_color: str = "#ff0000"
    save_conversation: bool = False

    # Search Settings
    search_engine: str = "duckduckgo"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HywCoreConfig":
        """Helper to load from dict, ignoring unknown keys"""
        valid_keys = cls.__annotations__.keys()
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered_data)
