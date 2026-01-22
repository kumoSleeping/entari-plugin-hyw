"""
hyw_core.core - Main HywCore Class

Provides the unified LLM query interface and search capabilities.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable, Awaitable

from loguru import logger

from .config import HywCoreConfig, ModelConfig
from .pipeline import ModularPipeline
from .search import SearchService
from .stages.base import StageContext


@dataclass
class QueryRequest:
    """Request for the query interface."""
    user_input: str
    images: List[str] = field(default_factory=list)  # base64 encoded images
    conversation_history: List[Dict] = field(default_factory=list)
    model_name: Optional[str] = None  # Override model
    
    # Optional callbacks
    send_notification: Optional[Callable[[str], Awaitable[None]]] = None


@dataclass
class QueryResponse:
    """Response from the query interface."""
    success: bool
    content: str  # Markdown response
    image_path: Optional[str] = None  # Path to rendered image
    
    # Statistics
    usage: Dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    total_time: float = 0.0
    
    # References
    references: List[Dict[str, Any]] = field(default_factory=list)
    page_references: List[Dict[str, Any]] = field(default_factory=list)
    image_references: List[Dict[str, Any]] = field(default_factory=list)
    
    # Trace information
    stages_trace: Dict[str, Any] = field(default_factory=dict)
    
    # Error handling
    error: Optional[str] = None
    should_refuse: bool = False
    refuse_reason: str = ""
    
    # Debug/Save
    web_results: List[Dict[str, Any]] = field(default_factory=list)
    stages_used: List[Dict[str, Any]] = field(default_factory=list)



class HywCore:
    """
    HYW Core Service.
    
    Provides the unified LLM query interface (/q command) and search capabilities.
    
    Usage:
        from hyw_core import HywCore, HywCoreConfig, QueryRequest
        
        config = HywCoreConfig.from_yaml("config.yaml")
        core = HywCore(config)
        
        response = await core.query(QueryRequest(
            user_input="What is Python?",
            images=[],
            conversation_history=[]
        ))
    """
    
    def __init__(
        self, 
        config: HywCoreConfig,
        send_func: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        """
        Initialize HywCore.
        
        Args:
            config: HywCoreConfig instance
            send_func: Optional callback for sending notifications
        """
        self.config = config
        self._send_func = send_func
        
        # Create search service
        self._search_service = SearchService(config)
        
        # Create pipeline
        self._pipeline = ModularPipeline(
            config=config,
            search_service=self._search_service,
            send_func=send_func
        )
        
        # Create renderer (lazy init)
        self._renderer = None
        
        logger.info("HywCore initialized")
    
    async def _ensure_renderer(self):
        """Lazy initialize renderer."""
        if self._renderer is None:
            from .browser_control import ContentRenderer
            self._renderer = ContentRenderer(headless=self.config.headless)
            await self._renderer.start()
    
    async def query(
        self, 
        request: QueryRequest,
        output_path: Optional[str] = None
    ) -> QueryResponse:
        """
        Unified query interface.
        
        This is the main entry point for /q commands.
        
        Args:
            request: QueryRequest with user input, images, history
            output_path: Optional path to save rendered image
            
        Returns:
            QueryResponse with content, rendered image path, and metadata
        """
        start_time = time.time()
        
        try:
            # Override model if specified
            model_name = request.model_name or self.config.model_name
            
            # Use notification callback from request if provided
            send_func = request.send_notification or self._send_func
            if send_func and self._pipeline._send_func != send_func:
                self._pipeline._send_func = send_func
            
            # Execute pipeline
            result = await self._pipeline.execute(
                user_input=request.user_input,
                conversation_history=request.conversation_history,
                model_name=model_name,
                images=request.images if request.images else None
            )
            
            total_time = time.time() - start_time
            
            # Check for refusal
            if result.get("should_refuse"):
                return QueryResponse(
                    success=True,
                    content="",
                    should_refuse=True,
                    refuse_reason=result.get("refuse_reason", ""),
                    total_time=total_time
                )
            
            # Extract response data
            content = result.get("llm_response", "")
            structured = result.get("structured_response", {})
            billing = result.get("billing_info", {})
            
            usage = {
                "input_tokens": billing.get("input_tokens", 0),
                "output_tokens": billing.get("output_tokens", 0)
            }
            
            # Calculate cost
            model_cfg = self.config.get_model_config("main")
            cost = (
                usage["input_tokens"] * (model_cfg.input_price or 0) / 1_000_000 +
                usage["output_tokens"] * (model_cfg.output_price or 0) / 1_000_000
            )
            
            # Build response
            response = QueryResponse(
                success=True,
                content=content,
                usage=usage,
                cost=cost,
                total_time=total_time,
                references=structured.get("references", []),
                page_references=structured.get("page_references", []),
                image_references=structured.get("image_references", []),
                stages_trace=result.get("trace", {}),
                web_results=result.get("web_results", []),
                stages_used=result.get("stages_used", [])
            )
            
            # Render image if output path provided
            if output_path and content:
                await self._ensure_renderer()
                
                render_success = await self._renderer.render(
                    markdown_content=content,
                    output_path=output_path,
                    stats=result.get("stats", {}),
                    references=result.get("references", []),
                    page_references=result.get("page_references", []),
                    image_references=result.get("image_references", []),
                    stages_used=result.get("stages_used", []),
                    theme_color=self.config.theme_color
                )
                
                if render_success:
                    response.image_path = output_path
            
            return response
            
        except Exception as e:
            logger.error(f"HywCore query failed: {e}")
            return QueryResponse(
                success=False,
                content="",
                error=str(e),
                total_time=time.time() - start_time
            )
    
    async def search(
        self,
        queries: List[str],
        engine: Optional[str] = None,
        limit: int = 10
    ) -> List[List[Dict[str, Any]]]:
        """
        Independent search interface.
        
        For future step-by-step search functionality.
        
        Args:
            queries: List of search queries
            engine: Optional search engine override
            limit: Results per query
            
        Returns:
            List of search results for each query
        """
        # TODO: Support engine override per-call
        return await self._search_service.search_batch(queries)

    async def screenshot(self, url: str) -> Optional[str]:
        """
        Capture full page screenshot of a URL.
        Returns: base64 string or None
        """
        # Default to full_page=True as requested for /w command
        return await self._search_service.screenshot_url(url, full_page=True)
    
    async def screenshot_batch(self, urls: List[str]) -> List[Optional[str]]:
        """
        Capture full page screenshots of multiple URLs concurrently.
        Returns: list of base64 strings (None for failed ones)
        """
        return await self._search_service.screenshot_urls_batch(urls, full_page=True)
    
    async def fetch_pages(
        self,
        urls: List[str],
        include_screenshot: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch multiple pages.
        
        Args:
            urls: List of URLs to fetch
            include_screenshot: Whether to capture screenshots
            
        Returns:
            List of page data dicts
        """
        return await self._search_service.fetch_pages_batch(
            urls, 
            include_screenshot=include_screenshot
        )
    
    async def render(
        self,
        markdown_content: str,
        output_path: str,
        **kwargs
    ) -> bool:
        """
        Render markdown to image.
        
        Args:
            markdown_content: Markdown to render
            output_path: Path to save image
            **kwargs: Additional render options
            
        Returns:
            True if successful
        """
        await self._ensure_renderer()
        return await self._renderer.render(
            markdown_content=markdown_content,
            output_path=output_path,
            theme_color=kwargs.pop("theme_color", self.config.theme_color),
            **kwargs
        )
    
    async def close(self):
        """Close all resources."""
        if self._renderer:
            await self._renderer.close()
        await self._pipeline.close()
        logger.info("HywCore closed")
