from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class HywConfigData:
    """Runtime configuration for HywAgent."""

    # LLM Settings
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "gpt-4o"
    temperature: float = 0.5

    # Browser Settings
    headless: bool = True

    # Behavior
    language: str = "Simplified Chinese"
    theme_color: str = "#ff0000"
    save_conversation: bool = False

    # Search Settings
    search_engine: str = "duckduckgo"
    jina_key: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HywConfigData":
        """Helper to load from dict, ignoring unknown keys"""
        valid_keys = cls.__annotations__.keys()
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered_data)
