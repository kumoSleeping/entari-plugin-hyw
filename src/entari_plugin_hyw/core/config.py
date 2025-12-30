from dataclasses import dataclass
from typing import Optional, Dict, Any, List

@dataclass
class HYWConfig:
    api_key: str
    model_name: str
    vision_model_name: Optional[str] = None
    vision_api_key: Optional[str] = None
    vision_base_url: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    fusion_mode: bool = False 
    save_conversation: bool = False
    headless: bool = True
    intruct_model_name: Optional[str] = None
    intruct_api_key: Optional[str] = None
    intruct_base_url: Optional[str] = None
    search_base_url: str = "https://lite.duckduckgo.com/lite/?q={query}"
    image_search_base_url: str = "https://duckduckgo.com/?q={query}&iax=images&ia=images"
    search_params: Optional[str] = None  # e.g. "&kl=cn-zh" for China region
    search_limit: int = 8 
    extra_body: Optional[Dict[str, Any]] = None
    temperature: float = 0.4
    max_turns: int = 10
    icon: str = "openai"  # logo for primary model
    vision_icon: Optional[str] = None  # logo for vision model (falls back to icon when absent)
    instruct_icon: Optional[str] = None # logo for instruct model
    enable_browser_fallback: bool = False
    vision_system_prompt: Optional[str] = None
    intruct_system_prompt: Optional[str] = None
    agent_system_prompt: Optional[str] = None
    input_price: Optional[float] = None  # $ per 1M input tokens
    output_price: Optional[float] = None  # $ per 1M output tokens
    vision_input_price: Optional[float] = None
    vision_output_price: Optional[float] = None
    intruct_input_price: Optional[float] = None
    intruct_output_price: Optional[float] = None
  