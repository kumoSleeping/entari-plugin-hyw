"""
Stage Base Classes

Abstract base classes for pipeline stages.
Each stage is a self-contained unit of work.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI


@dataclass
class StageContext:
    """Shared context passed between stages."""
    user_input: str
    images: List[str] = field(default_factory=list)
    conversation_history: List[Dict] = field(default_factory=list)
    instruct_history: List[Dict] = field(default_factory=list)  # History for Instruct stage rounds
    
    # Accumulated data
    web_results: List[Dict] = field(default_factory=list)
    agent_context: str = ""
    review_context: str = "" # Context passed from Instruct to Review stage
    
    # Mode info (set by Instruct stage)
    task_list: List[str] = field(default_factory=list)
    
    # Control flags
    should_refuse: bool = False
    refuse_reason: str = ""
    selected_mode: str = "fast"  # "fast" or "deepsearch"
    
    # ID counter for unified referencing
    global_id_counter: int = 0
    
    # Model capabilities
    image_input_supported: bool = True
    
    # Search timing
    search_time: float = 0.0
    
    def next_id(self) -> int:
        """Get next global ID."""
        self.global_id_counter += 1
        return self.global_id_counter


@dataclass
class StageResult:
    """Result from a stage execution."""
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    trace: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class BaseStage(ABC):
    """Abstract base class for pipeline stages."""
    
    def __init__(self, config: Any, search_service: Any, client: AsyncOpenAI):
        self.config = config
        self.search_service = search_service
        self.client = client
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Stage name for logging and tracing."""
        pass
    
    @abstractmethod
    async def execute(self, context: StageContext) -> StageResult:
        """
        Execute the stage.
        
        Args:
            context: Shared context with accumulated data
            
        Returns:
            StageResult with success status, data, usage, and trace info
        """
        pass
    
    def _client_for(self, api_key: Optional[str], base_url: Optional[str]) -> AsyncOpenAI:
        """Get or create client with custom credentials."""
        if api_key or base_url:
            return AsyncOpenAI(
                base_url=base_url or self.config.base_url,
                api_key=api_key or self.config.api_key
            )
        return self.client
