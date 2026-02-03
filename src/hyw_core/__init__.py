"""
hyw-core - Core LLM Pipeline and Browser Automation

This package provides:
- HywCore: Main service class with unified query interface
- HywCoreConfig: Configuration management with standalone/passthrough support
- QueryRequest/QueryResponse: Request/response data classes
- SearchService: Web search abstraction
- ModularPipeline: LLM pipeline orchestration
- browser_control: Browser automation subpackage

Usage:
    from hyw_core import HywCore, HywCoreConfig, QueryRequest
    
    # Standalone usage with YAML config
    config = HywCoreConfig.from_yaml("config.yaml")
    core = HywCore(config)
    
    response = await core.query(QueryRequest(
        user_input="What is Python?",
        images=[],
        conversation_history=[]
    ))
    
    # Passthrough from parent package
    config = HywCoreConfig.from_dict({
        "model_name": parent_config.model_name,
        "api_key": parent_config.api_key,
        ...
    })
"""

__version__ = "4.0.0-rc8"

# Core classes
from .core import HywCore, QueryRequest, QueryResponse

# Configuration
from .config import HywCoreConfig, ModelConfig

# Pipeline components
from .pipeline import ModularPipeline
from .tools.duckduckgo_search import DuckDuckGoSearchService as SearchService

# Stage components
from .stages import (
    BaseStage,
    StageContext,
    StageResult,

    SummaryStage,
)

# Definitions
from .definitions import (
    SUMMARY_REPORT_SP,
    get_refuse_answer_tool,
)

# Browser control is available as subpackage
from . import browser_control

__all__ = [
    # Version
    "__version__",
    
    # Core
    "HywCore",
    "QueryRequest", 
    "QueryResponse",
    
    # Configuration
    "HywCoreConfig",
    "ModelConfig",
    
    # Pipeline
    "ModularPipeline",
    "SearchService",
    
    # Stages
    "BaseStage",
    "StageContext",
    "StageResult",

    "SummaryStage",
    
    # Definitions
    "SUMMARY_REPORT_SP",
    "get_refuse_answer_tool",

    
    # Subpackage
    "browser_control",
]
