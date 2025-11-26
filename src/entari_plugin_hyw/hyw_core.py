import asyncio
import html
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

import httpx
from loguru import logger
from openai import AsyncOpenAI

# Try to import Playwright and Trafilatura
try:
    import trafilatura
    from playwright.async_api import async_playwright, Browser, Playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    trafilatura = None
    async_playwright = None
    Browser = None
    Playwright = None

# --- Constants & Prompts ---

# --- Constants & Prompts ---

VISION_EXPERT_SYSTEM_PROMPT = """你是一个专业的图像分析专家。

 [核心任务]
 - 请智能分析图片内容，根据图片类型自主选择侧重点。
 - **文字优先原则**：如果图片包含清晰的文字（如文档、截图、海报、对话记录等），或者用户的意图明显是获取文字信息，请将 **OCR文字识别** 作为核心任务。
   - 必须完整、准确地转录所有可见文字，不要遗漏。
   - 视觉描述作为补充，仅需简要说明图片类型（如"这是一张聊天记录截图"）。
 - 视觉补充：如果图片几乎没有文字，或者文字仅为背景点缀，请重点描述图片的视觉内容（物体、场景、人物、动作、氛围等）。

 [输出格式]
 - 直接输出分析结果。
 - 如果识别到文字，请使用清晰的格式列出。
"""

BASE_SYSTEM_PROMPT = """你是一个智能AI助手, 你的目的是帮助用户解决问题.
你拥有强大的思维链能力，在回答前请先进行深度的思考和隐形规划。

 [核心原则 - 必须严格遵守]
 - 永远使用中文回答
 - 语言简洁、语气客观专业、描述详精练抓重点
 - 绝对不允许使用除代码框外的markdown语法（**、*、`、#、-等符号）
 - 如果需要给出代码, 请添加到代码框内, 只给出部分代码即可, 尽可能减少回复字数
 - 回复带有紧扣结果相关的「补充推测」, 帮助用户进行下一步行动
 
 [安全审查]
 - 禁止讨论政策、国家领导人、政治体制等敏感话题的搜索与验证计划、新闻、历史事件
 - 过于敏感的话题, 规划时请谨慎
 - 过于色情、暴力、血腥等内容, 请谨慎处理, 避免直接描述

 [搜索与验证原则]
 - web 搜索优先使用 bing + google 双页面混合验证
 - 避免搜索 x.com 、 csdn 等不准确信息, 尽可能使用权威网站、相关项目官方网站
 - 搜索内容指向不同相关项目时, 尝试理解关系, 请避免混为一谈
 - 禁止导航到搜索引擎页面, 你可以直接导航到相关官网或权威网站
 - 可以同时启动多个工具查看不同页面, 提高效率
 - 人名、地名、组织名等关键信息优先验证, 只相信权威网站、相关项目官方网站
 - 存在视觉分析专家信息时, 不要尝试通过角色、人物特征进行搜索验证、直接利用视觉分析结果回答. 但如果视觉分析中有文字存在，可以对文字内容进行搜索, 抓住重点补充
 - 分步验证思想: 先确认A, 通过A确认B或C. 验证重点：指出需要特别验证的事实、数据或来源.
 
 [使用以下工具来获取页面和验证信息]
 {tools_desc}
 
 [针对搜索结果的回复要求]
 - 根据搜索结果给出准确回答，忽略浏览器广告、自动纠错提示等多余信息
 - 减少"根据搜索结果"、"未发现相关信息"等无意义表述
 - 由于搜索客观实效性, 避免 `预计` `大概` `可能` 等词汇
 - 不能使用 `教程` `怎么办` 等词汇进行搜索, 这些词汇会导致搜索结果偏离主题, 而且非官方信息居多

 [推测]
 - 回复推测时也要使得语气平稳、陈述
 - 回复推测语句简短, 通常在10个字左右
 - 一些合适的推测示例方向: /1 深入研究 /2 了解更多关于 /3 继续深度搜索 /4 解决方案 /1 官方文档的最佳实践 /2 给出实际代码片段 /3 获取页面完整内容...

 [最终回复格式]
 [LLM Agent] >>
<纯文本详细解释>
<(如果需要提供代码)```&lt;代码语言>
<代码>
 ```>
 
 [Next?] >>
 /1 <回复推测1>
 /2 <回复推测2>
 /3 <回复推测3>
 /4 <回复推测4>
"""

# --- Configuration ---

@dataclass
class HYWConfig:
    api_key: str
    model_name: str
    base_url: str = "https://openrouter.ai/api/v1"
    save_conversation: bool = False
    headless: bool = True
    
    # Browser Tool Configuration
    browser_tool: str = "jina"  # "jina" or "playwright"
    jina_api_key: Optional[str] = None
    
    vision_model_name: Optional[str] = None
    vision_base_url: Optional[str] = None
    vision_api_key: Optional[str] = None
    
    extra_body: Optional[Dict[str, Any]] = None

# --- Browser Tool ---

class BrowserTool:
    def __init__(self, config: HYWConfig):
        self.config = config
        self.playwright: Optional[Any] = None
        self.browser: Optional[Any] = None
        
        if self.config.browser_tool == "playwright" and not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Browser tool set to 'playwright' but playwright/trafilatura is not installed. Please install with 'pip install entari-plugin-hyw[playwright]' or set browser_tool to 'jina'.")
            
        if not PLAYWRIGHT_AVAILABLE and self.config.browser_tool != "jina":
             logger.warning("Playwright not installed. Local browser navigation disabled.")

    async def navigate(self, url: str) -> str:
        """Navigate to a URL and return the page content with fallback mechanism"""
        
        # Determine primary and secondary methods
        can_use_playwright = PLAYWRIGHT_AVAILABLE
        
        if self.config.browser_tool == "jina":
            primary_method = self._navigate_jina
            primary_name = "Jina"
            
            if can_use_playwright:
                secondary_method = self._navigate_playwright
                secondary_name = "Playwright"
            else:
                secondary_method = None
                secondary_name = None
        elif self.config.browser_tool == "playwright":
            primary_method = self._navigate_playwright
            primary_name = "Playwright"
            secondary_method = self._navigate_jina
            secondary_name = "Jina"
        else:
            # Default to Jina if unknown
            logger.warning(f"Unknown browser_tool '{self.config.browser_tool}', defaulting to Jina")
            primary_method = self._navigate_jina
            primary_name = "Jina"
            secondary_method = None
            secondary_name = None
            
        # Try primary method
        content = await primary_method(url)
        
        # Check for failure (assuming error messages start with "Error")
        if content.startswith("Error") and secondary_method:
            logger.warning(f"{primary_name} failed: {content}. Falling back to {secondary_name}...")
            content = await secondary_method(url)
             
        return content

    async def _navigate_jina(self, url: str) -> str:
        """Navigate using Jina AI"""
        try:
            logger.info(f"Jina AI navigating to: {url}")
            headers = {}
            if self.config.jina_api_key:
                headers["Authorization"] = f"Bearer {self.config.jina_api_key}"
                
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"https://r.jina.ai/{url}", headers=headers)
                if resp.status_code == 200:
                    content = resp.text
                    logger.info(f"Successfully fetched {len(content)} chars from {url} via Jina")
                    return content
                else:
                    return f"Error navigating to {url} via Jina: Status {resp.status_code}"
        except Exception as e:
            logger.error(f"Jina navigation failed: {e}")
            return f"Error navigating to {url} via Jina: {str(e)}"

    async def _ensure_browser(self):
        """Ensure Playwright browser is initialized"""
        if not PLAYWRIGHT_AVAILABLE:
            return

        if self.playwright is None:
            self.playwright = await async_playwright().start()
        
        if self.browser is None:
            self.browser = await self.playwright.chromium.launch(
                headless=self.config.headless,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"]
            )
            logger.info("Playwright browser initialized")

    async def _navigate_playwright(self, url: str) -> str:
        """Navigate using Playwright with a fresh context/page"""
        if not PLAYWRIGHT_AVAILABLE:
            return "Error: Playwright not installed"
            
        await self._ensure_browser()
        
        if not self.browser:
            return "Error: Browser not initialized"

        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        
        # Inject script to hide navigator.webdriver
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()
        
        try:
            logger.info(f"Playwright navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Wait a bit for dynamic content
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            # Get page content
            html_content = await page.content()
            
            # Use trafilatura for extraction
            content = trafilatura.extract(
                html_content,
                include_links=False,
                include_images=False,
                include_tables=False,
                include_comments=False,
                output_format="markdown"
            )
            
            # Fallback
            if not content:
                content = await page.evaluate("() => document.body.innerText")
                
            logger.info(f"Successfully fetched {len(content) if content else 0} chars from {url}")
            return content if content else "Error: Empty content"
            
        except Exception as e:
            logger.error(f"Playwright navigation failed: {e}")
            return f"Error navigating to {url}: {str(e)}"
        finally:
            await page.close()
            await context.close()
            
    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


# --- Core Class ---

class HYW:
    def __init__(self, config: HYWConfig):
        self.config = config
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        
        self._init_clients()
        self.browser_tool = BrowserTool(config)
        self._init_tools()
        
        logger.info(f"HYW initialized - Save Conversation: {config.save_conversation}, Browser Tool: {config.browser_tool}")

    def _init_clients(self):
        # Vision Client
        if self.config.vision_base_url:
            self.vision_client = AsyncOpenAI(
                base_url=self.config.vision_base_url,
                api_key=self.config.vision_api_key or self.config.api_key
            )
            model = self.config.vision_model_name or self.config.model_name
            logger.info(f"Vision client created - Endpoint: {self.config.vision_base_url}, Model: {model}")
        else:
            self.vision_client = self.client
            model = self.config.vision_model_name or self.config.model_name
            logger.info(f"Vision using main client - Model: {model}")

    def _init_tools(self):
        self.tools = []
        
        self.tools.append({
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": "Navigate to a URL and return the page content. Use this to search or view pages.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string", 
                            "description": "The URL to navigate to. For searching, use the search engine URL with the query."
                        }
                    },
                    "required": ["url"]
                }
            }
        })
            
        self.tools_desc = "\n".join([f"- {t['function']['name']}" for t in self.tools])

    async def analyze_images(self, images: List[str]) -> str:
        """Analyze images and return description"""
        if not images:
            return ""
            
        try:
            logger.info(f"Starting image analysis - Count: {len(images)}")
            
            img_content: List[Dict[str, Any]] = [{'type': 'text', 'text': '请分析这些图片'}]
            for img in images:
                img_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}})

            img_messages = [
                {"role": "system", "content": VISION_EXPERT_SYSTEM_PROMPT},
                {"role": "user", "content": img_content}
            ]
            
            model = self.config.vision_model_name or self.config.model_name
            img_resp = await self.vision_client.chat.completions.create(
                model=model,
                messages=img_messages
            )
            if img_resp.choices[0].message.content:
                logger.info(f"Image analysis complete")
                return img_resp.choices[0].message.content
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return ""
        return ""

    async def call_tool(self, tool_call) -> str:
        func_name = tool_call.function.name
        # Decode HTML entities in arguments before parsing
        args_str = html.unescape(tool_call.function.arguments)
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            return f"Error: Invalid JSON arguments for tool {func_name}"

        if func_name == "browser_navigate":
            if not self.browser_tool:
                 return "Error: Browser tool is disabled"
            
            url = args.get("url")
            if url:
                return await self.browser_tool.navigate(url)
            return "Error: Missing URL argument"
        return f"Error: Unknown tool {func_name}"

    def _tool_msg(self, tool_call_id: str, content: Any, is_error: bool = False, elapsed_time: Optional[float] = None) -> Dict[str, Any]:
        msg_content = f"错误: {content}" if is_error else str(content)
        if elapsed_time is not None:
            msg_content = f"[已运行: {elapsed_time:.2f}s] {msg_content}"
        return {
            "role": "tool", 
            "tool_call_id": tool_call_id, 
            "content": msg_content
        }

    async def _run_tool_isolated(self, tool_call, agent_start_time: float) -> Dict[str, Any]:
        tool_start = time.time()
        try:
            result = await self.call_tool(tool_call)
            tool_duration = time.time() - tool_start
            total_elapsed = time.time() - agent_start_time
            logger.info(f"Tool {tool_call.function.name} finished in {tool_duration:.2f}s (Total since start: {total_elapsed:.2f}s)")
            return self._tool_msg(tool_call.id, result, elapsed_time=total_elapsed)
        except Exception as e:
            total_elapsed = time.time() - agent_start_time
            logger.error(f"Tool failed: {e}")
            return self._tool_msg(tool_call.id, e, is_error=True, elapsed_time=total_elapsed)

    def _format_extra_content(self, content: Any) -> str:
        """Format extra content like reasoning or annotations"""
        return str(content)

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

    def _append_stats_info(self, content: str, stats: Dict[str, Any], start_time: float) -> str:
        current_duration = time.time() - start_time
        
        if not content or not content.strip():
            content = "[ERROR] \n>> 抱歉，获取到的内容可能包含敏感信息，暂时无法显示完整结果。"
        
        # Build stats parts
        vision_duration = stats.get("vision_duration", 0)
        if vision_duration > 0:
            time_parts = [f"[V:{vision_duration:.2f}s/{current_duration:.2f}s]"]
        else:
            time_parts = [f"[{current_duration:.2f}s]"]
        
        # Tools
        search_count = stats.get('search_results', 0)
        web_count = stats.get('web_pages_opened', 0)
        tools_parts = []
        if search_count > 0:
            tools_parts.append(f"[S:{search_count}]")
        if web_count > 0:
            tools_parts.append(f"[W:{web_count}]")
            
        stats_info = f"\n[Stats] :: {' '.join(tools_parts)} {' '.join(time_parts)}"
        return content + stats_info

    async def agent(self, user_input: str, conversation_history: Optional[List[Dict[str, Any]]] = None, images: Optional[List[str]] = None) -> Dict[str, Any]:
        start_time = time.time()
        
        stats = {
            "llm_calls": 0,
            "search_results": 0,
            "web_pages_opened": 0,
            "total_time": 0.0,
            "vision_duration": 0.0
        }
        
        # Vision/OCR Analysis
        image_analysis = ""
        if images:
            v_start = time.time()
            image_analysis = await self.analyze_images(images)
            stats["vision_duration"] = time.time() - v_start
        
        system_prompt = BASE_SYSTEM_PROMPT.format(tools_desc=self.tools_desc)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        
        if image_analysis:
            messages.append({"role": "system", "content": f"[图片分析报告]\n{image_analysis}"})
        
        if conversation_history:
            messages.extend([m for m in conversation_history if m.get("role") != "system"])
        
        messages.append({"role": "user", "content": user_input})

        logger.info(f"Processing: {user_input[:50]}...")
        
        try:
            for _ in range(25):
                # Retry mechanism for API calls
                max_retries = 3
                resp = None
                last_error = None
                
                for attempt in range(max_retries):
                    try:
                        stats["llm_calls"] += 1
                        resp = await self.client.chat.completions.create(
                            model=self.config.model_name, 
                            messages=messages, 
                            tools=self.tools if self.tools else None, 
                            tool_choice="auto" if self.tools else None,
                            extra_body=self.config.extra_body
                        )
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            logger.warning(f"API call failed (attempt {attempt + 1}/{max_retries}): {e}")
                            await asyncio.sleep(2)
                        else:
                            logger.error(f"API call failed after {max_retries} attempts: {e}")
                            
                if resp is None:
                    if last_error:
                        self._save_conversation_debug(messages)
                        logger.error(f"Final API failure: {last_error}")
                        return {
                            "llm_response": f"抱歉，AI 提供商似乎出现了故障，重试多次后仍然失败。\n错误信息: {str(last_error)}", 
                            "conversation_history": messages,
                            "stats": stats
                        }
                    return {"llm_response": "Error: Failed to get response from LLM", "conversation_history": messages, "stats": stats}

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
                    stats["web_pages_opened"] += len([tc for tc in msg.tool_calls if tc.function.name == "browser_navigate"])
                    tasks = [self._run_tool_isolated(tc, start_time) for tc in msg.tool_calls]
                    results = await asyncio.gather(*tasks)
                    messages.extend(results)
                elif msg.content:
                    logger.success("Conversation completed")
                    self._save_conversation_debug(messages)
                    filtered_history = [m for m in messages if m.get("role") != "system"]
                    final_response = self._append_stats_info(msg.content, stats, start_time)
                    return {"llm_response": final_response, "conversation_history": filtered_history, "stats": stats}
            
            # Max turns reached
            self._save_conversation_debug(messages)
            final_response = self._append_stats_info("Max turns reached", stats, start_time)
            return {"llm_response": final_response, "conversation_history": messages, "stats": stats}
        finally:
            pass
