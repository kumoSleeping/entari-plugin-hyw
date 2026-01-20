"""
Vision Stage

Generates image description using a vision-capable model.
The description is then passed as context to subsequent stages.
"""

import time
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from .stage_base import BaseStage, StageContext, StageResult
from .definitions import VISION_DESCRIPTION_SP


class VisionStage(BaseStage):
    """
    Vision Stage: Generate image description.
    
    Takes user images and text, calls a vision model to produce
    a detailed description of the image content.
    """
    
    @property
    def name(self) -> str:
        return "Vision"
    
    async def execute(
        self, 
        context: StageContext, 
        images: List[str] = None
    ) -> StageResult:
        """Generate image description."""
        start_time = time.time()
        
        if not images:
            return StageResult(
                success=True,
                data={"description": ""},
                trace={"skipped": True, "reason": "No images provided"}
            )
        
        # Get model config for vision stage
        model_cfg = self.config.get_model_config("vision")
        model = model_cfg.get("model_name")
        
        if not model:
            logger.warning("VisionStage: No vision model configured, skipping")
            return StageResult(
                success=True,
                data={"description": ""},
                trace={"skipped": True, "reason": "No vision model configured"}
            )
        
        client = self._client_for(
            api_key=model_cfg.get("api_key"),
            base_url=model_cfg.get("base_url")
        )
        
        # Build user content with images
        user_text = context.user_input or "请描述这张图片"
        user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
        
        for img_b64 in images:
            url = f"data:image/jpeg;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
            user_content.append({"type": "image_url", "image_url": {"url": url}})
        
        messages = [
            {"role": "system", "content": VISION_DESCRIPTION_SP},
            {"role": "user", "content": user_content}
        ]
        
        try:
            logger.info(f"VisionStage: Calling model '{model}' with {len(images)} image(s)")
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,  # Lower temperature for factual description
                extra_body=model_cfg.get("extra_body"),
            )
        except Exception as e:
            logger.error(f"VisionStage LLM error: {e}")
            return StageResult(
                success=False,
                error=str(e),
                data={"description": ""},
                trace={"error": str(e)}
            )
        
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] = getattr(response.usage, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(response.usage, "completion_tokens", 0) or 0
        
        description = (response.choices[0].message.content or "").strip()
        
        logger.info(f"VisionStage: Generated description ({len(description)} chars)")
        
        return StageResult(
            success=True,
            data={"description": description},
            usage=usage,
            trace={
                "model": model,
                "provider": model_cfg.get("model_provider") or "Unknown",
                "usage": usage,
                "output": description,
                "time": time.time() - start_time,
                "images_count": len(images),
            }
        )
