"""
Summary Stage

Generates final response based on gathered information.
Different output formats for different modes.
"""

import time
import re
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from .base import BaseStage, StageContext, StageResult
from ..definitions import SUMMARY_REPORT_SP, get_refuse_answer_tool


class SummaryStage(BaseStage):
    """
    Summary Stage: Generate final response.
    """
    
    @property
    def name(self) -> str:
        return "Summary"
    
    async def execute(
        self, 
        context: StageContext, 
        images: List[str] = None
    ) -> StageResult:
        """Generate summary."""
        start_time = time.time()
        
        # Format context from web results
        web_content = self._format_web_content(context)
        
        # Tools
        refuse_tool = get_refuse_answer_tool()
        full_context = f"{context.agent_context}\n\n{web_content}"
        
        # Select prompt
        language = getattr(self.config, "language", "Simplified Chinese")
        
        system_prompt = SUMMARY_REPORT_SP.format(
            language=language
        )
        
        # Build Context Message
        context_message = f"## Web Search & Page Content\n\n```context\n{full_context}\n```"
        
        
        # Build user content
        user_text = context.user_input or "..."
        if images:
            # Add image context message for multimodal input
            image_context = f"[System: The user has provided {len(images)} image(s). Please analyze these images together with the text query to provide a comprehensive response.]"
            user_content: List[Dict[str, Any]] = [{"type": "text", "text": f"{image_context}\n\n{user_text}"}]
            for img_b64 in images:
                url = f"data:image/jpeg;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_text
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_message},
            {"role": "user", "content": user_content}
        ]
        
        # Get model config
        model_cfg = self.config.get_model_config("main")
        
        client = self._client_for(
            api_key=model_cfg.api_key,
            base_url=model_cfg.base_url
        )
        
        model = model_cfg.model_name or self.config.model_name
        
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self.config.temperature,

                extra_body=getattr(self.config, "summary_extra_body", None),
                tools=[refuse_tool],
                tool_choice="auto",
            )
        except Exception as e:
            logger.error(f"SummaryStage LLM error: {e}")
            return StageResult(
                success=False,
                error=str(e),
                data={"content": f"Error generating summary: {e}"}
            )
        
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] = getattr(response.usage, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(response.usage, "completion_tokens", 0) or 0
        
        # Handle Tool Calls (Refusal)
        tool_calls = response.choices[0].message.tool_calls
        if tool_calls:
            for tc in tool_calls:
                if tc.function.name == "refuse_answer":
                    import json
                    try:
                        args = json.loads(tc.function.arguments)
                        reason = args.get("reason", "Refused")
                        context.should_refuse = True
                        context.refuse_reason = reason
                        return StageResult(
                            success=True,
                            data={"content": f"Refused: {reason}"},
                            usage=usage,
                            trace={"skipped": True, "reason": reason}
                        )
                    except: pass
        
        content = (response.choices[0].message.content or "").strip()
        
        return StageResult(
            success=True,
            data={"content": content},
            usage=usage,
            trace={
                "model": model,
                "provider": model_cfg.model_provider or "Unknown",
                "usage": usage,
                "system_prompt": system_prompt,
                "context_message": context_message,  # Includes vision description + search results
                "output": content,
                "time": time.time() - start_time,
                "images_count": len(images) if images else 0,
            }
        )
    
    def _strip_links(self, text: str) -> str:
        """Strip markdown links [text](url) -> text and remove bare URLs."""
        # Replace [text](url) with text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # Remove bare URLs (http/https) roughly, trying to preserve surrounding text if possible?
        # A simple pattern for http/s
        text = re.sub(r'https?://\S+', '', text)
        return text

    def _format_web_content(self, context: StageContext) -> str:
        """Format web results for summary prompt."""
        if not context.web_results:
            return ""
        
        # Sort results: pages first, then raw searches, then snippets
        def get_priority(item_type):
            if item_type == "page": return 0
            if item_type == "search_raw_page": return 1
            return 2  # search (snippets)
            
        sorted_results = sorted(
            context.web_results, 
            key=lambda x: get_priority(x.get("_type"))
        )
        
        lines = []
        seen_urls = set()
        
        for res in sorted_results:
            type_ = res.get("_type")
            idx = res.get("_id")
            title = (res.get("title", "") or "").strip()
            url = res.get("url", "")
            
            # Deduplicate items by URL (keep higher priority item only)
            if url:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
            
            # url = res.get("url", "") # Removed as requested
            
            if type_ == "page":
                content = (res.get("content", "") or "").strip()
                content = self._strip_links(content)
                lines.append(f"[{idx}] Title: {title}\nContent:\n{content}\n")
            elif type_ == "search":
                snippet = (res.get("content", "") or "").strip()
                snippet = self._strip_links(snippet)
                lines.append(f"[{idx}] Title: {title}\nSnippet: {snippet}\n")
        
        return "\n".join(lines)
