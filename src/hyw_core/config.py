"""
hyw_core.config - Configuration Management

Provides standalone configuration for hyw-core with optional passthrough from parent packages.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional


@dataclass
class ModelConfig:
    """Configuration for a specific model."""
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None
    model_provider: Optional[str] = None
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    image_input: bool = True


@dataclass
class HywCoreConfig:
    """
    Core configuration for hyw-core.
    
    Can be used standalone or with passthrough from parent packages.
    
    Usage:
        # Standalone from YAML
        config = HywCoreConfig.from_yaml("config.yaml")
        
        # Passthrough from parent
        config = HywCoreConfig.from_dict({
            "model_name": parent_config.model_name,
            "api_key": parent_config.api_key,
            ...
        })
    """
    
    # LLM Configuration
    models: List[Dict[str, Any]] = field(default_factory=list)
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.4
    
    # Stage-specific model overrides
    instruct_model: Optional[str] = None
    instruct_api_key: Optional[str] = None
    instruct_base_url: Optional[str] = None
    instruct_extra_body: Optional[Dict[str, Any]] = None
    
    summary_model: Optional[str] = None
    summary_api_key: Optional[str] = None
    summary_base_url: Optional[str] = None
    summary_extra_body: Optional[Dict[str, Any]] = None
    
    # Search Configuration
    search_engine: str = "duckduckgo"
    search_limit: int = 10
    blocked_domains: List[str] = field(default_factory=list)
    
    # Browser Configuration
    headless: bool = True
    fetch_timeout: float = 20.0
    
    # Output Configuration
    language: str = "Simplified Chinese"
    theme_color: str = "#ef4444"
    
    # Pricing (for cost estimation)
    input_price: float = 0.0
    output_price: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HywCoreConfig":
        """
        Create config from dictionary.
        
        Used for passthrough from parent packages.
        Filters out unknown fields to allow flexible passthrough.
        """
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in field_names}
        return cls(**filtered_data)
    
    @classmethod
    def from_yaml(cls, path: str) -> "HywCoreConfig":
        """
        Load config from YAML file.
        
        Used for standalone usage.
        """
        import yaml
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)
    
    def get_model_config(self, stage: str) -> ModelConfig:
        """
        Get resolved model config for a stage.
        
        Args:
            stage: "instruct", "qa", or "main" (summary)
        
        Returns:
            ModelConfig with resolved settings
        """
        # Determine primary and secondary stage config keys
        if stage == "instruct":
            primary_prefix = "instruct_"
            secondary_prefix = None
        elif stage == "qa":
            primary_prefix = "qa_"
            secondary_prefix = "instruct_"
        else:  # "main" / summary
            primary_prefix = "summary_"
            secondary_prefix = None
        
        def resolve(field_name: str, is_essential: bool = True):
            """Resolve a field with fallback: Primary -> Secondary -> Root."""
            # Try primary
            if primary_prefix:
                val = getattr(self, f"{primary_prefix}{field_name}", None)
                if val:
                    return val
            
            # Try secondary
            if secondary_prefix:
                val = getattr(self, f"{secondary_prefix}{field_name}", None)
                if val:
                    return val
            
            # Fallback to root
            return getattr(self, field_name, None)
        
        return ModelConfig(
            model_name=resolve("model") or resolve("model_name") or self.model_name,
            api_key=resolve("api_key") or self.api_key,
            base_url=resolve("base_url") or self.base_url,
            extra_body=resolve("extra_body"),
            model_provider=resolve("model_provider"),
            input_price=resolve("input_price") or self.input_price,
            output_price=resolve("output_price") or self.output_price,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        import dataclasses
        return dataclasses.asdict(self)
