from typing import Any, Dict, List, Optional
from loguru import logger
from .config import HYWConfig
from .pipeline import ProcessingPipeline

class HYW:
    """
    V2 Core Wrapper (Facade).
    Delegates all logic to ProcessingPipeline.
    Ensures safe lifecycle management.
    """
    def __init__(self, config: HYWConfig):
        self.config = config
        # No persistent pipeline - we create one per request to ensure thread safety
        # self.pipeline = ProcessingPipeline(config) 
        logger.info(f"HYW V2 (Ironclad) initialized - Model: {config.model_name}")

    async def agent(self, user_input: str, conversation_history: List[Dict] = None, images: List[str] = None, 
                   selected_model: str = None, selected_vision_model: str = None, local_mode: bool = False) -> Dict[str, Any]:
        """
        Main entry point for the plugin (called by __init__.py).
        Creates a fresh pipeline instance for each request to avoid state contamination (race conditions).
        """
        pipeline = ProcessingPipeline(self.config)
        try:
            # Delegate completely to pipeline
            result = await pipeline.execute(
                user_input,
                conversation_history or [],
                model_name=selected_model,
                images=images,
                selected_vision_model=selected_vision_model,
            )
            return result
        finally:
             await pipeline.close()

    async def close(self):
        """Explicit async close method. NO __del__."""
        # Close shared resources
        try:
            from ..utils.search import close_shared_crawler
            await close_shared_crawler()
        except Exception:
            pass

    # Legacy Compatibility (optional attributes just to prevent blind attribute errors if referenced externally)
    # in V2 we strongly discourage accessing internal tools directly.
