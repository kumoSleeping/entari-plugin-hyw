import asyncio
import html
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from openai import AsyncOpenAI

from .config import HYWConfig
from ..utils.mcp_playwright import MCPPlaywrightManager
from ..utils.search import SearchService
from ..utils.prompts import (
    AGENT_SYSTEM_PROMPT,
    AGENT_SYSTEM_PROMPT_INTRUCT_VISION_ADD,
    AGENT_SYSTEM_PROMPT_MCP_ADD,
    AGENT_SYSTEM_PROMPT_SEARCH_ADD,
    INTRUCT_SYSTEM_PROMPT,
    INTRUCT_SYSTEM_PROMPT_VISION_ADD,
    VISION_SYSTEM_PROMPT,
)

@asynccontextmanager
async def _null_async_context():
    yield None


class ProcessingPipeline:
    """
    Core pipeline (vision -> instruct/search -> agent).
    """

    def __init__(self, config: HYWConfig):
        self.config = config
        self.search_service = SearchService(config)
        self.client = AsyncOpenAI(base_url=self.config.base_url, api_key=self.config.api_key)
        # Build Playwright MCP args with headless flag if configured
        playwright_args = getattr(self.config, "playwright_mcp_args", None)
        if playwright_args is None:
            playwright_args = ["-y", "@playwright/mcp@latest"]
            # Add --headless flag if headless mode is enabled
            if getattr(self.config, "headless", True):
                playwright_args.append("--headless")
        
        self.mcp_playwright = MCPPlaywrightManager(
            command=getattr(self.config, "playwright_mcp_command", "npx"),
            args=playwright_args,
        )

        self.web_search_tool = {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for text and images.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
        self.grant_mcp_playwright_tool = {
            "type": "function",
            "function": {
                "name": "grant_mcp_playwright",
                "description": "Decide whether to grant Playwright MCP browser tools to the agent for this request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "grant": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["grant"],
                },
            },
        }

    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict],
        model_name: str = None,
        images: List[str] = None,
        vision_model_name: str = None,
        selected_vision_model: str = None,
    ) -> Dict[str, Any]:
        """
        1) Vision: summarize images once (no image persistence).
        2) Intruct: run web_search and decide whether to grant Playwright MCP tools.
        3) Agent: normally no tools; if granted, allow Playwright MCP tools (max 6 rounds; step 5 nudge, step 6 forced).
        """
        start_time = time.time()
        stats = {"start_time": start_time, "tool_calls_count": 0}
        # Token usage tracking for billing
        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        active_model = model_name or self.config.model_name

        current_history = conversation_history
        final_response_content = ""
        structured: Dict[str, Any] = {}

        try:
            logger.info(f"Pipeline: Starting workflow for '{user_input}' using {active_model}")

            trace: Dict[str, Any] = {
                "vision": None,
                "intruct": None,
                "agent": None,
            }

            # Vision stage
            vision_text = ""
            vision_start = time.time()
            vision_time = 0
            vision_cost = 0.0
            vision_usage = {}
            if images:
                vision_model = (
                    selected_vision_model
                    or vision_model_name
                    or getattr(self.config, "vision_model_name", None)
                    or active_model
                )
                vision_prompt_tpl = getattr(self.config, "vision_system_prompt", None) or VISION_SYSTEM_PROMPT
                vision_prompt = vision_prompt_tpl.format(user_msgs=user_input or "[图片]")
                vision_text, vision_usage = await self._run_vision_stage(
                    user_input=user_input,
                    images=images,
                    model=vision_model,
                    prompt=vision_prompt,
                )
                # Add vision usage with vision-specific pricing
                usage_totals["input_tokens"] += vision_usage.get("input_tokens", 0)
                usage_totals["output_tokens"] += vision_usage.get("output_tokens", 0)
                
                # Calculate Vision Cost
                v_in_price = float(getattr(self.config, "vision_input_price", None) or getattr(self.config, "input_price", 0.0) or 0.0)
                v_out_price = float(getattr(self.config, "vision_output_price", None) or getattr(self.config, "output_price", 0.0) or 0.0)
                if v_in_price > 0 or v_out_price > 0:
                     vision_cost = (vision_usage.get("input_tokens", 0) / 1_000_000 * v_in_price) + (vision_usage.get("output_tokens", 0) / 1_000_000 * v_out_price)

                vision_time = time.time() - vision_start
                
                trace["vision"] = {
                    "model": vision_model,
                    "base_url": getattr(self.config, "vision_base_url", None) or self.config.base_url,
                    "prompt": vision_prompt,
                    "user_input": user_input or "",
                    "images_count": len(images or []),
                    "output": vision_text,
                    "usage": vision_usage,
                    "time": vision_time,
                    "cost": vision_cost
                }

            # Intruct + pre-search
            instruct_start = time.time()
            instruct_model = getattr(self.config, "intruct_model_name", None) or active_model
            instruct_text, search_payloads, intruct_trace, intruct_usage, search_time = await self._run_instruct_stage(
                user_input=user_input,
                vision_text=vision_text,
                model=instruct_model,
            )
            instruct_time = time.time() - instruct_start
            
            # Calculate Instruct Cost
            instruct_cost = 0.0
            i_in_price = float(getattr(self.config, "intruct_input_price", None) or getattr(self.config, "input_price", 0.0) or 0.0)
            i_out_price = float(getattr(self.config, "intruct_output_price", None) or getattr(self.config, "output_price", 0.0) or 0.0)
            if i_in_price > 0 or i_out_price > 0:
                instruct_cost = (intruct_usage.get("input_tokens", 0) / 1_000_000 * i_in_price) + (intruct_usage.get("output_tokens", 0) / 1_000_000 * i_out_price)
            
            # Add instruct usage
            usage_totals["input_tokens"] += intruct_usage.get("input_tokens", 0)
            usage_totals["output_tokens"] += intruct_usage.get("output_tokens", 0)
            
            intruct_trace["time"] = instruct_time
            intruct_trace["cost"] = instruct_cost
            trace["intruct"] = intruct_trace

            explicit_mcp_intent = "mcp" in (user_input or "").lower()
            grant_requested = bool(intruct_trace.get("grant_mcp_playwright", False))
            grant_mcp = bool(grant_requested and explicit_mcp_intent)
            intruct_trace["explicit_mcp_intent"] = explicit_mcp_intent
            intruct_trace["grant_effective"] = grant_mcp
            if grant_requested and not explicit_mcp_intent:
                logger.info("Intruct requested MCP grant, but user did not express MCP intent. Grant ignored.")
            if grant_mcp:
                logger.warning(f"MCP Playwright granted for this request: reason={intruct_trace.get('grant_reason')!r}")

            # Start agent loop
            agent_start_time = time.time()
            current_history.append({"role": "user", "content": user_input or "..."})

            max_steps = 6
            step = 0
            agent_trace_steps: List[Dict[str, Any]] = []
            last_system_prompt = ""

            mcp_tools_openai: Optional[List[Dict[str, Any]]] = None
            if grant_mcp:
                mcp_tools_openai = await self.mcp_playwright.tools_openai()
                if not mcp_tools_openai:
                    logger.warning("MCP Playwright was granted but tools are unavailable (connect failed).")
                    grant_mcp = False

            # Agent loop - always runs regardless of MCP grant status
            while step < max_steps:
                step += 1
                logger.info(f"Pipeline: Agent step {step}/{max_steps}")

                if step == 5:
                    current_history.append(
                        {
                            "role": "system",
                            "content": "System: [Next Step Final] Please start consolidating the answer; the next step must be the final response.",
                        }
                    )

                agent_tools = mcp_tools_openai if grant_mcp else None
                tools_desc = "\n".join([t["function"]["name"] for t in (agent_tools or [])]) if agent_tools else ""

                user_msgs_text = user_input or ""

                search_msgs_text = self._format_search_msgs(search_payloads)
                has_search_results = bool(search_payloads)  # Only append if search was actually performed

                # Build agent system prompt with modular ADD sections
                agent_prompt_tpl = getattr(self.config, "agent_system_prompt", None) or AGENT_SYSTEM_PROMPT
                system_prompt = agent_prompt_tpl.format(user_msgs=user_msgs_text)
                
                # Append vision text if available
                if vision_text:
                    system_prompt += AGENT_SYSTEM_PROMPT_INTRUCT_VISION_ADD.format(vision_msgs=vision_text)
                
                # Append search results if search was performed and has results
                if has_search_results:
                    system_prompt += AGENT_SYSTEM_PROMPT_SEARCH_ADD.format(search_msgs=search_msgs_text)
                
                # Append MCP addon prompt when MCP is granted
                if grant_mcp and tools_desc:
                    system_prompt += AGENT_SYSTEM_PROMPT_MCP_ADD.format(tools_desc=tools_desc)
                
                last_system_prompt = system_prompt

                messages = [{"role": "system", "content": system_prompt}]
                messages.extend(current_history)

                tools_for_step = agent_tools if (agent_tools and step < max_steps) else None
                response, step_usage = await self._safe_llm_call(
                    messages=messages,
                    model=active_model,
                    tools=tools_for_step,
                    tool_choice="auto" if tools_for_step else None,
                )
                # Accumulate agent usage
                usage_totals["input_tokens"] += step_usage.get("input_tokens", 0)
                usage_totals["output_tokens"] += step_usage.get("output_tokens", 0)

                if response.tool_calls and tools_for_step:
                    tool_calls = response.tool_calls
                    stats["tool_calls_count"] += len(tool_calls)

                    plan_dict = response.model_dump() if hasattr(response, "model_dump") else response
                    current_history.append(plan_dict)

                    tasks = [self._safe_route_tool(tc, mcp_session=self.mcp_playwright if grant_mcp else None) for tc in tool_calls]
                    results = await asyncio.gather(*tasks)

                    step_trace = {
                        "step": step,
                        "tool_calls": [self._tool_call_to_trace(tc) for tc in tool_calls],
                        "tool_results": [],
                    }
                    for i, result in enumerate(results):
                        tc = tool_calls[i]
                        step_trace["tool_results"].append({"name": tc.function.name, "content": str(result)})
                        current_history.append(
                            {
                                "tool_call_id": tc.id,
                                "role": "tool",
                                "name": tc.function.name,
                                "content": str(result),
                            }
                        )
                    agent_trace_steps.append(step_trace)
                    continue

                final_response_content = response.content or ""
                current_history.append({"role": "assistant", "content": final_response_content})
                agent_trace_steps.append({"step": step, "final": True, "output": final_response_content})
                break

            if not final_response_content:
                final_response_content = "执行结束，但未生成内容。"

            structured = self._parse_tagged_response(final_response_content)
            final_content = structured.get("response") or final_response_content

            agent_time = time.time() - agent_start_time
            
            # Calculate Agent Cost (accumulated steps)
            agent_cost = 0.0
            a_in_price = float(getattr(self.config, "input_price", 0.0) or 0.0)
            a_out_price = float(getattr(self.config, "output_price", 0.0) or 0.0)
            
            # Agent usage is already in usage_totals, but that includes ALL stages.
            # We need just Agent tokens for Agent cost.
            # Agent inputs = Total inputs - Vision inputs - Instruct inputs
            agent_input_tokens = usage_totals["input_tokens"] - vision_usage.get("input_tokens", 0) - intruct_usage.get("input_tokens", 0)
            agent_output_tokens = usage_totals["output_tokens"] - vision_usage.get("output_tokens", 0) - intruct_usage.get("output_tokens", 0)
            
            if a_in_price > 0 or a_out_price > 0:
                agent_cost = (agent_input_tokens / 1_000_000 * a_in_price) + (agent_output_tokens / 1_000_000 * a_out_price)

            trace["agent"] = {
                "model": active_model,
                "base_url": self.config.base_url,
                "system_prompt": last_system_prompt,
                "steps": agent_trace_steps,
                "final_output": final_response_content,
                "mcp_granted": grant_mcp,
                "time": agent_time,
                "cost": agent_cost
            }
            trace_markdown = self._render_trace_markdown(trace)

            stats["total_time"] = time.time() - start_time
            stats["steps"] = step

            # Calculate billing info
            billing_info = {
                "input_tokens": usage_totals["input_tokens"],
                "output_tokens": usage_totals["output_tokens"],
                "total_cost": 0.0,
            }
            # Calculate cost if any pricing is configured
            input_price = getattr(self.config, "input_price", None) or 0.0
            output_price = getattr(self.config, "output_price", None) or 0.0
            
            if input_price > 0 or output_price > 0:
                # Price is per million tokens
                input_cost = (usage_totals["input_tokens"] / 1_000_000) * input_price
                output_cost = (usage_totals["output_tokens"] / 1_000_000) * output_price
                billing_info["total_cost"] = input_cost + output_cost
                # logger.info(f"Billing: {usage_totals['input_tokens']} in @ ${input_price}/M + {usage_totals['output_tokens']} out @ ${output_price}/M = ${billing_info['total_cost']:.6f}")

            # Build stages_used list for UI display
            # Order: Vision (if used) -> Search (if performed) -> Agent
            stages_used = []
            
            # Helper to infer icon from model name or base_url
            def infer_icon(model_name: str, base_url: str) -> str:
                model_lower = (model_name or "").lower()
                url_lower = (base_url or "").lower()
                
                if "deepseek" in model_lower or "deepseek" in url_lower:
                    return "deepseek"
                elif "claude" in model_lower or "anthropic" in url_lower:
                    return "anthropic"
                elif "gemini" in model_lower or "google" in url_lower:
                    return "google"
                elif "gpt" in model_lower or "openai" in url_lower:
                    return "openai"
                elif "qwen" in model_lower:
                    return "qwen"
                elif "openrouter" in url_lower:
                    return "openrouter"
                return "openai"  # Default fallback
            
            # Helper to infer provider from base_url
            def infer_provider(base_url: str) -> str:
                url_lower = (base_url or "").lower()
                if "openrouter" in url_lower:
                    return "OpenRouter"
                elif "openai" in url_lower:
                    return "OpenAI"
                elif "anthropic" in url_lower:
                    return "Anthropic"
                elif "google" in url_lower:
                    return "Google"
                elif "deepseek" in url_lower:
                    return "DeepSeek"
                return ""  # Empty string = don't show provider
            
            if trace.get("vision"):
                v = trace["vision"]
                v_model = v.get("model", "")
                v_base_url = v.get("base_url", "") or self.config.base_url
                stages_used.append({
                    "name": "Vision",
                    "model": v_model,
                    "icon_config": getattr(self.config, "vision_icon", None) or infer_icon(v_model, v_base_url),
                    "provider": infer_provider(v_base_url),
                    "time": v.get("time", 0),
                    "cost": v.get("cost", 0.0)
                })
            
            if trace.get("intruct"):
                i = trace["intruct"]
                i_model = i.get("model", "")
                i_base_url = i.get("base_url", "") or self.config.base_url
                stages_used.append({
                    "name": "Instruct",
                    "model": i_model,
                    "icon_config": getattr(self.config, "intruct_icon", None) or infer_icon(i_model, i_base_url),
                    "provider": infer_provider(i_base_url),
                    "time": i.get("time", 0),
                    "cost": i.get("cost", 0.0)
                })

            # Show Search stage only when search was actually performed
            if search_payloads:
                # Use dedicated SearXNG metadata as requested
                stages_used.append({
                    "name": "Search",
                    "model": "SearXNG",
                    "icon_config": "search", # Ensure mapping exists or handle specially in render
                    "provider": "SearXNG",
                    "time": search_time,
                    "cost": 0.0 # Search is free in this plugin
                })
            
            if trace.get("agent"):
                a = trace["agent"]
                a_model = a.get("model", "") or active_model
                a_base_url = a.get("base_url", "") or self.config.base_url
                stages_used.append({
                    "name": "Agent",
                    "model": a_model,
                    "icon_config": getattr(self.config, "icon", None) or infer_icon(a_model, a_base_url),
                    "provider": infer_provider(a_base_url),
                    "time": a.get("time", 0),
                    "cost": a.get("cost", 0.0)
                })

            return {
                "llm_response": final_content,
                "structured_response": structured,
                "stats": stats,
                "model_used": active_model,
                "vision_model_used": (selected_vision_model or getattr(self.config, "vision_model_name", None)) if images else None,
                "conversation_history": current_history,
                "trace_markdown": trace_markdown,
                "billing_info": billing_info,
                "stages_used": stages_used,
            }

        except Exception as e:
            logger.exception("Pipeline Critical Failure")
            return {
                "llm_response": f"I encountered a critical error: {e}",
                "stats": stats,
                "error": str(e),
            }

    async def _safe_route_tool(self, tool_call, mcp_session=None):
        """Wrapper for safe concurrent execution."""
        try:
            return await asyncio.wait_for(self._route_tool(tool_call, mcp_session=mcp_session), timeout=15.0)
        except asyncio.TimeoutError:
            return "Error: Tool execution timed out (15s limit)."
        except Exception as e:
            return f"Error: Tool execution failed: {e}"

    def _parse_tagged_response(self, text: str) -> Dict[str, Any]:
        """Parse response for references and mcp blocks."""
        parsed = {"response": "", "references": [], "mcp_steps": []}
        if not text:
            return parsed

        import re
        
        remaining_text = text

        # Parse references block
        ref_block_match = re.search(r'```references\s*(.*?)\s*```', remaining_text, re.DOTALL | re.IGNORECASE)
        if ref_block_match:
            ref_content = ref_block_match.group(1).strip()
            for line in ref_content.split("\n"):
                line = line.strip()
                link_match = re.search(r"\[(.*?)\]\((.*?)\)", line)
                if link_match:
                    parsed["references"].append({"title": link_match.group(1), "url": link_match.group(2)})
            remaining_text = remaining_text.replace(ref_block_match.group(0), "").strip()

        # Parse mcp block - supports format:
        # [icon] tool_name
        #   description
        mcp_block_match = re.search(r'```mcp\s*(.*?)\s*```', remaining_text, re.DOTALL | re.IGNORECASE)
        if mcp_block_match:
            mcp_content = mcp_block_match.group(1).strip()
            lines = mcp_content.split("\n")
            current_step = None
            
            for line in lines:
                line_stripped = line.strip()
                if not line_stripped: continue

                # New Format: "1. [icon] name: description" OR "[icon] name: description"
                # Regex details:
                # ^(?:(?:\d+\.|[-*])\s+)?  -> Optional numbering (1. or - or *)
                # \[(\w+)\]                 -> Icon in brackets [icon] -> group 1
                # \s+                       -> separating space
                # ([^:]+)                   -> Tool Name (chars before colon) -> group 2
                # :                         -> Colon separator
                # \s*(.+)                   -> Description -> group 3
                new_format_match = re.match(r'^(?:(?:\d+\.|[-*])\s+)?\[(\w+)\]\s+([^:]+):\s*(.+)$', line_stripped)
                
                # Old/Flexible Format: "[icon] name" (description might be on next line)
                flexible_match = re.match(r'^(?:(?:\d+\.|[-*])\s+)?\[(\w+)\]\s+(.+)$', line_stripped)

                if new_format_match:
                    if current_step: parsed["mcp_steps"].append(current_step)
                    current_step = {
                        "icon": new_format_match.group(1).lower(),
                        "name": new_format_match.group(2).strip(),
                        "description": new_format_match.group(3).strip()
                    }
                elif flexible_match:
                    # Could be just "[icon] name" without description, or mixed
                    if current_step: parsed["mcp_steps"].append(current_step)
                    current_step = {
                        "icon": flexible_match.group(1).lower(),
                        "name": flexible_match.group(2).strip(),
                        "description": ""
                    }
                elif line.startswith("  ") and current_step:
                    # Indented description line (continuation)
                    if current_step["description"]:
                        current_step["description"] += " " + line.strip()
                    else:
                        current_step["description"] = line.strip()
                elif line_stripped and not line_stripped.startswith("[") and current_step is None:
                     # Plain text line without icon, treat as name if no current step
                     # (This handles cases where LLM forgets brackets but lists steps)
                     if current_step: parsed["mcp_steps"].append(current_step)
                     current_step = {
                         "icon": "default", 
                         "name": line_stripped,
                         "description": ""
                     }
            
            if current_step:
                parsed["mcp_steps"].append(current_step)
            remaining_text = remaining_text.replace(mcp_block_match.group(0), "").strip()

        parsed["response"] = remaining_text.strip()
        return parsed

    async def _safe_llm_call(self, messages, model, tools=None, tool_choice=None, client: Optional[AsyncOpenAI] = None):
        """
        Wrap LLM calls with timeout and error handling.
        Returns a tuple of (message, usage_dict) where usage_dict contains input_tokens and output_tokens.
        """
        try:
            return await asyncio.wait_for(
                self._do_llm_request(messages, model, tools, tool_choice, client=client or self.client),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error("LLM Call Timed Out")
            return type("obj", (object,), {"content": "Error: The model took too long to respond.", "tool_calls": None})(), {"input_tokens": 0, "output_tokens": 0}
        except Exception as e:
            logger.error(f"LLM Call Failed: {e}")
            return type("obj", (object,), {"content": f"Error: Model failure ({e})", "tool_calls": None})(), {"input_tokens": 0, "output_tokens": 0}

    async def _do_llm_request(self, messages, model, tools, tool_choice, client: AsyncOpenAI):
        try:
            payload_debug = json.dumps(messages)
            logger.info(f"LLM Request Payload Size: {len(payload_debug)} chars")
        except Exception:
            pass

        t0 = time.time()
        logger.info("LLM Request SENT to API...")
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=self.config.temperature,
        )
        logger.info(f"LLM Request RECEIVED after {time.time() - t0:.2f}s")
        
        # Extract usage information
        usage = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(response, "usage") and response.usage:
            usage["input_tokens"] = getattr(response.usage, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(response.usage, "completion_tokens", 0) or 0
        
        return response.choices[0].message, usage

    async def _route_tool(self, tool_call, mcp_session=None):
        name = tool_call.function.name
        args = json.loads(html.unescape(tool_call.function.arguments))

        if name == "web_search":
            query = args.get("query")
            text_task = self.search_service.search(query)
            image_task = self.search_service.image_search(query)
            results = await asyncio.gather(text_task, image_task)
            return json.dumps({"web_results": results[0], "image_results": results[1][:5]}, ensure_ascii=False)

        if name == "grant_mcp_playwright":
            return "OK"  # Minimal response, LLM already knows what it passed

        if mcp_session is not None and name.startswith("browser_"):
            return await mcp_session.call_tool_text(name, args or {})

        return f"Unknown tool {name}"

    async def _run_vision_stage(self, user_input: str, images: List[str], model: str, prompt: str) -> Tuple[str, Dict[str, int]]:
        """Returns (vision_text, usage_dict)."""
        content_payload: List[Dict[str, Any]] = [{"type": "text", "text": user_input or ""}]
        for img_b64 in images:
            url = f"data:image/png;base64,{img_b64}" if not img_b64.startswith("data:") else img_b64
            content_payload.append({"type": "image_url", "image_url": {"url": url}})

        client = self._client_for(
            api_key=getattr(self.config, "vision_api_key", None),
            base_url=getattr(self.config, "vision_base_url", None),
        )
        response, usage = await self._safe_llm_call(
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content_payload}],
            model=model,
            client=client,
        )
        return (response.content or "").strip(), usage

    async def _run_instruct_stage(
        self, user_input: str, vision_text: str, model: str
    ) -> Tuple[str, List[str], Dict[str, Any], Dict[str, int], float]:
        """Returns (instruct_text, search_payloads, trace_dict, usage_dict, search_time)."""
        tools = [self.web_search_tool, self.grant_mcp_playwright_tool]
        tools_desc = "\n".join([t["function"]["name"] for t in tools])

        prompt_tpl = getattr(self.config, "intruct_system_prompt", None) or INTRUCT_SYSTEM_PROMPT
        prompt = prompt_tpl.format(user_msgs=user_input or "", tools_desc=tools_desc)
        if vision_text:
            prompt = f"{prompt}\\n\\n{INTRUCT_SYSTEM_PROMPT_VISION_ADD.format(vision_msgs=vision_text)}"

        client = self._client_for(
            api_key=getattr(self.config, "intruct_api_key", None),
            base_url=getattr(self.config, "intruct_base_url", None),
        )

        history: List[Dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input or "..."},
        ]

        response, usage = await self._safe_llm_call(
            messages=history,
            model=model,
            tools=tools,
            tool_choice="auto",
            client=client,
        )

        search_payloads: List[str] = []
        intruct_trace: Dict[str, Any] = {
            "model": model,
            "base_url": getattr(self.config, "intruct_base_url", None) or self.config.base_url,
            "prompt": prompt,
            "user_input": user_input or "",
            "vision_add": vision_text or "",
            "grant_mcp_playwright": False,
            "grant_reason": "",
            "tool_calls": [],
            "tool_results": [],
            "output": "",
        }
        
        search_time = 0.0

        if response.tool_calls:
            plan_dict = response.model_dump() if hasattr(response, "model_dump") else response
            history.append(plan_dict)

            tasks = [self._safe_route_tool(tc) for tc in response.tool_calls]
            
            # Measure search/tool execution time
            st = time.time()
            results = await asyncio.gather(*tasks)
            search_time = time.time() - st
            
            for i, result in enumerate(results):
                tc = response.tool_calls[i]
                history.append(
                    {"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": str(result)}
                )
                intruct_trace["tool_calls"].append(self._tool_call_to_trace(tc))
                intruct_trace["tool_results"].append({"name": tc.function.name, "content": str(result)})
                if tc.function.name == "web_search":
                    search_payloads.append(str(result))
                elif tc.function.name == "grant_mcp_playwright":
                    try:
                        args = json.loads(html.unescape(tc.function.arguments))
                    except Exception:
                        args = {}
                    intruct_trace["grant_mcp_playwright"] = bool(args.get("grant"))
                    intruct_trace["grant_reason"] = str(args.get("reason") or "")
            # No second LLM call: tool-call arguments already include the extracted keywords/query
            # and the grant decision; avoid wasting tokens/time.
            intruct_trace["output"] = ""
            intruct_trace["usage"] = usage
            return "", search_payloads, intruct_trace, usage, search_time

        intruct_trace["output"] = (response.content or "").strip()
        intruct_trace["usage"] = usage
        return "", search_payloads, intruct_trace, usage, 0.0

    def _format_search_msgs(self, search_payloads: List[str]) -> str:
        """
        Keep only tool results for the agent (no extra Intruct free-text output).
        Also compress payloads to reduce prompt tokens.
        """
        merged_web: List[Dict[str, str]] = []
        merged_img: List[Dict[str, str]] = []

        for payload in search_payloads or []:
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            merged_web.extend(obj.get("web_results") or [])
            merged_img.extend(obj.get("image_results") or [])

        def dedupe(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
            seen = set()
            out = []
            for it in items:
                url = it.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                out.append(it)
            return out

        merged_web = dedupe(merged_web)[:6]
        merged_img = dedupe(merged_img)[:3]

        def clip(s: str, n: int) -> str:
            s = (s or "").strip()
            return s if len(s) <= n else s[: n - 1] + "…"

        compact_web = [
            {"title": clip(r.get("title", ""), 80), "url": r.get("url", ""), "content": clip(r.get("content", ""), 180)}
            for r in merged_web
        ]
        compact_img = [{"title": clip(r.get("title", ""), 80), "url": r.get("url", "")} for r in merged_img]

        return json.dumps({"web_results": compact_web, "image_results": compact_img}, ensure_ascii=False)

    def _client_for(self, api_key: Optional[str], base_url: Optional[str]) -> AsyncOpenAI:
        if api_key or base_url:
            return AsyncOpenAI(base_url=base_url or self.config.base_url, api_key=api_key or self.config.api_key)
        return self.client

    def _tool_call_to_trace(self, tool_call) -> Dict[str, Any]:
        try:
            args = json.loads(html.unescape(tool_call.function.arguments))
        except Exception:
            args = tool_call.function.arguments
        return {"id": getattr(tool_call, "id", None), "name": tool_call.function.name, "arguments": args}

    def _render_trace_markdown(self, trace: Dict[str, Any]) -> str:
        def fence(label: str, content: str) -> str:
            safe = (content or "").replace("```", "``\\`")
            return f"```{label}\n{safe}\n```"

        parts: List[str] = []
        parts.append("# Pipeline Trace\n")

        if trace.get("vision"):
            v = trace["vision"]
            parts.append("## Vision\n")
            parts.append(f"- model: `{v.get('model')}`")
            parts.append(f"- base_url: `{v.get('base_url')}`")
            parts.append(f"- images_count: `{v.get('images_count')}`\n")
            parts.append("### Prompt\n")
            parts.append(fence("text", v.get("prompt", "")))
            parts.append("\n### Output\n")
            parts.append(fence("text", v.get("output", "")))
            parts.append("")

        if trace.get("intruct"):
            t = trace["intruct"]
            parts.append("## Intruct\n")
            parts.append(f"- model: `{t.get('model')}`")
            parts.append(f"- base_url: `{t.get('base_url')}`\n")
            parts.append(f"- grant_mcp_playwright: `{bool(t.get('grant_mcp_playwright'))}`")
            if t.get("grant_reason"):
                parts.append(f"- grant_reason: `{t.get('grant_reason')}`")
            if "explicit_mcp_intent" in t:
                parts.append(f"- explicit_mcp_intent: `{bool(t.get('explicit_mcp_intent'))}`")
            if "grant_effective" in t:
                parts.append(f"- grant_effective: `{bool(t.get('grant_effective'))}`\n")
            parts.append("### Prompt\n")
            parts.append(fence("text", t.get("prompt", "")))
            if t.get("tool_calls"):
                parts.append("\n### Tool Calls\n")
                parts.append(fence("json", json.dumps(t.get("tool_calls"), ensure_ascii=False, indent=2)))
            if t.get("tool_results"):
                parts.append("\n### Tool Results\n")
                parts.append(fence("json", json.dumps(t.get("tool_results"), ensure_ascii=False, indent=2)))
            parts.append("\n### Output\n")
            parts.append(fence("text", t.get("output", "")))
            parts.append("")

        if trace.get("agent"):
            a = trace["agent"]
            parts.append("## Agent\n")
            parts.append(f"- model: `{a.get('model')}`")
            parts.append(f"- base_url: `{a.get('base_url')}`\n")
            parts.append(f"- mcp_granted: `{bool(a.get('mcp_granted'))}`\n")
            parts.append("### System Prompt\n")
            parts.append(fence("text", a.get("system_prompt", "")))
            parts.append("\n### Steps\n")
            parts.append(fence("json", json.dumps(a.get("steps", []), ensure_ascii=False, indent=2)))
            parts.append("\n### Final Output\n")
            parts.append(fence("text", a.get("final_output", "")))

        return "\n".join(parts).strip() + "\n"

    async def close(self):
        try:
            await self.mcp_playwright.close()
        except Exception:
            pass

    async def warmup_mcp(self) -> bool:
        ok = await self.mcp_playwright.ensure_connected()
        if ok:
            logger.info("MCP Playwright connected (warmup).")
        else:
            logger.warning("MCP Playwright warmup failed.")
        return ok
