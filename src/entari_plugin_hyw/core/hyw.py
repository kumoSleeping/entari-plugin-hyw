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
        self.pipeline = ProcessingPipeline(config)
        logger.info(f"HYW V2 (Ironclad) initialized - Model: {config.model_name}")

    async def agent(self, user_input: str, conversation_history: List[Dict] = None, images: List[str] = None, 
                   selected_model: str = None, selected_vision_model: str = None, local_mode: bool = False) -> Dict[str, Any]:
        """
        Main entry point for the plugin (called by __init__.py).
        """
        # Note: 'images' handling is skipped for V2 initial stability MVP as per user focus on 'search hangs'.
        # We can re-integrate vision later, but for now we focus on Text/Search stability.
        
        # Delegate completely to pipeline
        result = await self.pipeline.execute(
            user_input,
            conversation_history or [],
            model_name=selected_model,
            images=images,
            selected_vision_model=selected_vision_model,
        )
        return result

    async def close(self):
        """Explicit async close method. NO __del__."""
        if self.pipeline:
            await self.pipeline.close()

    # Legacy Compatibility (optional attributes just to prevent blind attribute errors if referenced externally)
    # in V2 we strongly discourage accessing internal tools directly.
