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

__version__ = "1.0.0-alpha.1"

# Core classes
from .core import HywCore, QueryRequest, QueryResponse

# Configuration
from .config import HywCoreConfig, ModelConfig

# Pipeline components
from .pipeline import ModularPipeline
from .search import SearchService

# Stage components
from .stages import (
    BaseStage,
    StageContext,
    StageResult,
    InstructStage,
    SummaryStage,
)

# Definitions
from .definitions import (
    INSTRUCT_SP,
    SUMMARY_REPORT_SP,
    get_refuse_answer_tool,
    get_web_search_tool,
    get_crawl_page_tool,
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
    "InstructStage",
    "SummaryStage",
    
    # Definitions
    "INSTRUCT_SP",
    "SUMMARY_REPORT_SP",
    "get_refuse_answer_tool",
    "get_web_search_tool",
    "get_crawl_page_tool",
    
    # Subpackage
    "browser_control",
]
