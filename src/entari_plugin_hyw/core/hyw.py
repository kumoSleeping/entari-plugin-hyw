import asyncio
import html
import json
import time
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from ..utils.browser import BrowserTool
from ..utils.prompts import (
    BASE_SYSTEM_PROMPT, 
    VISION_EXPERT_SYSTEM_PROMPT, 
    ADDITIONAL_RULES_PROMPT, 
    FINAL_TURN_PROMPT,
    NO_WEB_PROMPT
)

@dataclass
class HYWConfig:
    api_key: str
    model_name: str
    models: List[Dict[str, Any]] = field(default_factory=list)
    base_url: str = "https://openrouter.ai/api/v1"
    save_conversation: bool = False
    headless: bool = True
    browser_tool: str = "playwright"
    icon: str = "openai"
    jina_api_key: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None
    enable_browser_fallback: bool = False

class HYW:
    def __init__(self, config: HYWConfig):
        self.config = config
        self._resolve_api_key()
        self.client = AsyncOpenAI(base_url=self.config.base_url, api_key=self.config.api_key)
        self.browser_tool = BrowserTool(config)
        self._init_tools()
        logger.info(f"HYW initialized - Model: {config.model_name}")

    def _resolve_api_key(self):
        if not self.config.api_key:
            # Try default model, then any model
            for m in sorted(self.config.models, key=lambda x: not x.get("is_default")):
                if m.get("api_key"):
                    self.config.api_key = m.get("api_key")
                    self.config.base_url = m.get("base_url", self.config.base_url)
                    break

    def _init_tools(self):
        self.tools = [{
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": "Navigate to a URL to search or view pages.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "The URL to navigate to."}},
                    "required": ["url"]
                }
            }
        }]
        if self.config.jina_api_key:
            self.tools.append({
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web using Jina AI.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "The search query"}},
                        "required": ["query"]
                    }
                }
            })
        self.tools_desc = "\n".join([f"- {t['function']['name']}" for t in self.tools])

    def _get_client(self, model_name: str) -> AsyncOpenAI:
        model_conf = next((m for m in self.config.models if m.get("name") == model_name), None)
        api_key = model_conf.get("api_key", self.config.api_key) if model_conf else self.config.api_key
        base_url = model_conf.get("base_url", self.config.base_url) if model_conf else self.config.base_url
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def analyze_images(self, images: List[str], model_name: Optional[str] = None) -> tuple[str, str]:
        if not images: return "", ""
        
        try:
            logger.info(f"Analyzing {len(images)} images")
            content = [{'type': 'text', 'text': '请分析这些图片'}]
            for img in images:
                url = img if img.startswith(("http", "data:")) else f"data:image/png;base64,{img}"
                content.append({"type": "image_url", "image_url": {"url": url}})

            # Determine model
            model = model_name or next((m["name"] for m in self.config.models if m.get("is_vision_default")), self.config.model_name)
            client = self._get_client(model)
            
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": VISION_EXPERT_SYSTEM_PROMPT}, {"role": "user", "content": content}]
            )
            return resp.choices[0].message.content or "", model
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return "", ""

    async def call_tool(self, tool_call) -> str:
        name = tool_call.function.name
        try:
            args = json.loads(html.unescape(tool_call.function.arguments))
        except json.JSONDecodeError:
            return f"Error: Invalid JSON for {name}"

        if name == "browser_navigate":
            return await self.browser_tool.navigate(args.get("url", "")) if args.get("url") else "Error: Missing URL"
        if name == "web_search":
            return await self.browser_tool.search(args.get("query", "")) if args.get("query") else "Error: Missing query"
        return f"Error: Unknown tool {name}"

    def _save_conversation_debug(self, messages: List[Dict[str, Any]]):
        """Save conversation history to JSON file for debugging"""
        if not self.config.save_conversation:
            return
        
        try:
            import os
            debug_dir = "saved_conversations"
            os.makedirs(debug_dir, exist_ok=True)
            
            timestamp = int(time.time())
            filename = os.path.join(debug_dir, f"conversation_{timestamp}.json")
            
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"Conversation saved to {filename}")
        except Exception as e:
            logger.warning(f"Failed to save conversation debug: {e}")

    def _clean_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Clean history for saving/returning: remove system/tool messages and tool_calls"""
        cleaned = []
        for msg in messages:
            if msg.get("role") in ("system", "tool"):
                continue
            
            new_msg = msg.copy()
            if "tool_calls" in new_msg:
                del new_msg["tool_calls"]
            
            # Skip empty messages (e.g. pure tool calls)
            if not new_msg.get("content"):
                continue
            
            cleaned.append(new_msg)
        return cleaned

    def _format_extra_content(self, annotations: Any) -> str:
        if isinstance(annotations, list):
            return "\n".join([str(a) for a in annotations])
        return str(annotations)

    async def _run_tool_isolated(self, tool_call, start_time: float) -> Dict[str, Any]:
        t_start = time.time()
        try:
            result = await self.call_tool(tool_call)
            duration = time.time() - t_start
            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": f"[已运行: {duration:.2f}s] {result}"
            }
        except Exception as e:
            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": f"Error executing tool {tool_call.function.name}: {str(e)}"
            }

    async def agent(self, user_input: str, conversation_history: List[Dict] = None, images: List[str] = None, 
                   selected_model: str = None, selected_vision_model: str = None, local_mode: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        stats = {"llm_calls": 0, "search_results": 0, "web_pages_opened": 0, "visited_domains": [], "vision_duration": 0.0, "tool_calls_count": 0}
        
        # Determine model
        model = selected_model or self.config.model_name
        max_turns = 5
        
        # Check default config if not selected
        if not selected_model:
            def_conf = next((m for m in self.config.models if m.get("is_default")), None)
            if def_conf:
                model = def_conf.get("name")
        
        # Update max_turns based on final model
        model_conf = next((m for m in self.config.models if m.get("name") == model), None)
        if model_conf:
            max_turns = model_conf.get("max_turns", 5)

        # Image Analysis
        vision_model = None
        if images:
            t0 = time.time()
            
            # Direct Vision Mode: Skip agent loop, get markdown description directly
            content = []
            if user_input:
                content.append({'type': 'text', 'text': user_input})
            else:
                content.append({'type': 'text', 'text': '请分析图片'})

            for img in images:
                url = img if img.startswith(("http", "data:")) else f"data:image/png;base64,{img}"
                content.append({"type": "image_url", "image_url": {"url": url}})

            # Determine model
            model = selected_vision_model or next((m["name"] for m in self.config.models if m.get("is_vision_default")), self.config.model_name)
            client = self._get_client(model)
            
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": VISION_EXPERT_SYSTEM_PROMPT}, {"role": "user", "content": content}]
                )
                description = resp.choices[0].message.content or ""
                vision_model = model
            except Exception as e:
                logger.error(f"Image analysis failed: {e}")
                description = f"图片分析失败: {e}"
                vision_model = model

            stats["vision_duration"] = time.time() - t0
            stats["time"] = time.time() - start_time
            
            # Construct structured response manually
            structured = {
                "response": description,
                "speculation": ["继续研究"],
                "references": []
            }
            
            # Construct history
            messages = []
            if conversation_history:
                messages.extend(conversation_history)
            messages.append({"role": "user", "content": user_input or "[图片]"})
            
            # Format history content like the main loop does
            history_content = description
            if structured.get("speculation"):
                history_content += "\n\n[Suggestions]:\n" + "\n".join([f"- {s}" for s in structured["speculation"]])
            
            messages.append({"role": "assistant", "content": history_content})
            
            clean_msgs = self._clean_history(messages)
            
            return {
                "llm_response": description,
                "conversation_history": clean_msgs,
                "stats": stats,
                "structured_response": structured,
                "model_used": None,
                "vision_model_used": vision_model
            }

        # Prepare Messages
        img_analysis = ""
        system_prompt = BASE_SYSTEM_PROMPT.format(tools_desc=self.tools_desc)
        tools_to_use = self.tools
        
        if local_mode:
            system_prompt = NO_WEB_PROMPT
            tools_to_use = None
            
        messages = [{"role": "system", "content": system_prompt}]
        if img_analysis: messages.append({"role": "system", "content": f"[图片分析报告]\n{img_analysis}"})
        
        user_turns = 1
        if conversation_history:
            user_turns = len([m for m in conversation_history if m.get("role") == "user"]) + 1
            messages.append({"role": "system", "content": ADDITIONAL_RULES_PROMPT})
            if user_turns >= max_turns: messages.append({"role": "system", "content": FINAL_TURN_PROMPT})
            messages.extend([m for m in conversation_history if m.get("role") != "system"])
            
        messages.append({"role": "user", "content": user_input})
        logger.info(f"Processing: {user_input[:50]}... (Model: {model})")

        # Main Loop
        for _ in range(25):
            stats["llm_calls"] += 1
            client = self._get_client(model)
            
            resp = None
            last_error = None
            # Retry logic for API calls
            for attempt in range(3):
                try:
                    resp = await client.chat.completions.create(
                        model=model, messages=messages, tools=tools_to_use or None, tool_choice="auto" if tools_to_use else None,
                        extra_body=self.config.extra_body
                    )
                    if resp and resp.choices:
                        break
                except Exception as e:
                    last_error = e
                    logger.warning(f"LLM call failed (attempt {attempt+1}/3): {e}")
                    if attempt < 2: await asyncio.sleep(1)

            if resp is None or not resp.choices:
                if last_error:
                    logger.error(f"Final API failure: {last_error}")
                else:
                    logger.error("Final API failure: Response or choices is None")
                
                clean_msgs = self._clean_history(messages)
                self._save_conversation_debug(clean_msgs)
                stats["time"] = time.time() - start_time
                return {
                    "llm_response": f"""抱歉, 虽然很不想承认，但AI提供商、开发者、部署配置总有一个出了问题:
错误信息: {str(last_error) if last_error else 'Response or choices is None'}""", 
                    "conversation_history": clean_msgs,
                    "stats": stats
                }

            msg = resp.choices[0].message
            msg_dict = msg.model_dump(exclude_none=True)
            
            # Process reasoning and annotations
            annotations = msg_dict.get('annotations')
            
            # Clean up response dict
            for key in ['reasoning_details', 'annotations', 'reasoning']:
                msg_dict.pop(key, None)
            
            # Add system message with search info if available
            if annotations:
                search_info = self._format_extra_content(annotations)
                try:
                    if isinstance(annotations, list):
                        stats["search_results"] += len(annotations)
                except Exception:
                    pass
                    
                system_msg = {
                    "role": "tool", 
                    "content": search_info,
                    "tool_call_id": "citation"
                }
                messages.append(system_msg)
            
            messages.append(msg_dict)
            
            logger.info(f"LLM Response: content={bool(msg.content)}, tools={bool(msg.tool_calls)}")

            if msg.tool_calls:
                stats["tool_calls_count"] += len(msg.tool_calls)
                stats["web_pages_opened"] += len([tc for tc in msg.tool_calls if tc.function.name == "browser_navigate"])
                
                # Extract domains for stats
                for tc in msg.tool_calls:
                    if tc.function.name == "browser_navigate":
                        try:
                            args_str = html.unescape(tc.function.arguments)
                            args = json.loads(args_str)
                            url = args.get("url", "")
                            match = re.search(r'https?://(?:www\.)?([^/.]+)', url)
                            if match:
                                stats["visited_domains"].append(match.group(1))
                        except Exception:
                            pass

                tasks = [self._run_tool_isolated(tc, start_time) for tc in msg.tool_calls]
                results = await asyncio.gather(*tasks)
                messages.extend(results)
            elif msg.content:
                logger.success("Conversation completed")
                
                # Final response
                content = msg.content or ""
                try:
                    # Robust JSON extraction: find first '{' and last '}'
                    start_idx = content.find("{")
                    end_idx = content.rfind("}")
                    
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        json_str = content[start_idx:end_idx+1]
                        structured = json.loads(json_str)
                        
                        # Manually format structured data into text for history
                        history_content = structured.get("response", "")
                        
                        # Append suggestions
                        if structured.get("speculation"):
                            history_content += "\n\n[Suggestions]:\n" + "\n".join([f"- {s}" for s in structured["speculation"]])
                            
                        # Append references
                        if structured.get("references"):
                            history_content += "\n\n[References]:\n" + "\n".join([f"- {r.get('title', 'Link')}: {r.get('url', '#')}" for r in structured["references"]])
                            
                        # Update the conversation history with the formatted text
                        if messages:
                            messages[-1]["content"] = history_content
                    else:
                        structured = {}
                except: structured = {}
                
                clean_msgs = self._clean_history(messages)
                stats["time"] = time.time() - start_time
                return {
                    "llm_response": structured.get("response", content),
                    "conversation_history": clean_msgs,
                    "stats": stats,
                    "structured_response": structured,
                    "model_used": model,
                    "vision_model_used": vision_model
                }
        
        # Max turns reached
        clean_msgs = self._clean_history(messages)
        self._save_conversation_debug(clean_msgs)
        stats["time"] = time.time() - start_time
        return {"llm_response": "Max turns reached", "conversation_history": clean_msgs, "stats": stats}
