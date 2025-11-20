import asyncio
import html
import json
import time
import httpx
import trafilatura
from typing import Optional
from openai import AsyncOpenAI
from playwright.async_api import async_playwright
from loguru import logger

class HYW:
    def __init__(self, api_key, model_name, base_url, search_engine, debug=False, 
                 vision_model_name=None, vision_base_url=None, headless=True, use_jina=False,
                 compress_content=False, compress_model_name=None, compress_base_url=None):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name
        self.search_engine = search_engine
        self.debug = debug
        self.headless = headless
        self.use_jina = use_jina
        
        # Compression configuration
        self.compress_content_enabled = compress_content
        self.compress_model_name = compress_model_name if compress_model_name else model_name
        self.compress_base_url = compress_base_url if compress_base_url else base_url
        
        if self.compress_content_enabled:
            if compress_base_url:
                self.compress_client = AsyncOpenAI(base_url=compress_base_url, api_key=api_key)
            else:
                self.compress_client = self.client
            logger.info(f"内容压缩已启用 - 模型: {self.compress_model_name}")
        
        logger.info(f"HYW 初始化 - Debug模式: {self.debug}, Jina解析: {self.use_jina}")
        
        # Browser state
        self.playwright = None
        self.browser = None
        
        # Vision analysis configuration
        self.vision_model_name = vision_model_name if vision_model_name else model_name
        self.vision_base_url = vision_base_url if vision_base_url else base_url
        
        # Create separate client if custom base_url is specified
        if vision_base_url:
            self.vision_client = AsyncOpenAI(
                base_url=vision_base_url,
                api_key=api_key
            )
            logger.info(f"视觉分析客户端已创建 - 端点: {vision_base_url}, 模型: {self.vision_model_name}")
        else:
            # Reuse main client when using same base_url
            self.vision_client = self.client
            if vision_model_name:
                logger.info(f"视觉分析使用主客户端 - 模型: {self.vision_model_name}")
            else:
                logger.info(f"视觉分析使用主客户端和主模型: {self.vision_model_name}")
        
        # Define tools locally
        self.tools = [
            {
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
            }
        ]
        
        self.tools_desc = "\n".join([f"- {t['function']['name']}" for t in self.tools])
        
        self.compress_expert_system_prompt = """你是一个专业的网页内容压缩专家。
[核心任务]
- 你的任务是阅读网页原始内容，提取并总结其中的核心信息。
- 去除广告、导航栏、版权声明、无关推荐等噪音信息。
- 保留正文的关键事实、数据、观点和逻辑结构。
- 如果是教程或技术文档，保留关键步骤和代码片段。
- 保持客观，不要添加个人评论。

[输出格式]
- 直接输出压缩后的内容。
"""

        self.vision_expert_system_prompt = """你是一个专业的图像分析专家。

 [核心任务]
 - 请智能分析图片内容，根据图片类型自主选择侧重点。
 - **文字优先原则**：如果图片包含清晰的文字（如文档、截图、海报、对话记录等），或者用户的意图明显是获取文字信息，请将 **OCR文字识别** 作为核心任务。
   - 必须完整、准确地转录所有可见文字，不要遗漏。
   - 视觉描述作为补充，仅需简要说明图片类型（如"这是一张聊天记录截图"）。
 - **视觉补充**：如果图片几乎没有文字，或者文字仅为背景点缀，请重点描述图片的视觉内容（物体、场景、人物、动作、氛围等）。

 [输出格式]
 - 直接输出分析结果。
 - 如果识别到文字，请使用清晰的格式列出。
"""

        self.base_system_prompt = f"""你是一个搜索和信息验证AI助手, 你的目的是从用户的给出的信息中提取关键词并加以解释说明, 帮助用户完成使用解释的方式完成问题.
你拥有强大的思维链能力，在回答前请先进行深度的思考和隐形规划。

 [时间管理]
 - 每次调用工具后, 会返回一个已运行的时间标记, 请注意你最佳运行时间: 一次回答最好不要超过1分钟
 - 如果用户是首次提出问题, 可以只通过多种搜索结果交叉回答, 尽可能20s以内解决问题
 - 用户第二次以上提出问题时，或第一次提出问题就要求深度思考, 则需要多次调用工具进行验证

 [核心原则 - 必须严格遵守]
 - 强制要求：收到任何问题后, 第一步必须调用 `browser_navigate` 工具导航到相关网页获取信息, 绝对不允许直接回答问题
 - 禁止导航到搜索引擎页面, 你可以直接导航到相关官网或权威网站
 - 可以同时启动多个工具查看不同页面, 提高效率
 - 人名、地名、组织名等关键信息优先验证, 只相信权威网站、相关项目官方网站
 - 存在视觉分析专家信息时, 不要尝试通过角色、人物特征进行搜索验证、直接利用视觉分析结果回答. 但如果视觉分析中有文字存在，可以对文字内容进行搜索, 抓住重点补充
 - 分步验证思想: 先确认A, 通过A确认B或C. 验证重点：指出需要特别验证的事实、数据或来源.
 
 [使用以下工具来获取页面和验证信息]
 {self.tools_desc}
 
  最终回复要求:
 - 300字以内, 输出信息尽可能少和精炼
 - 回答紧凑, 最少出现空行
 - 根据搜索结果给出准确回答，忽略浏览器广告、自动纠错提示等多余信息
 - 永远使用中文回答
 - 语言简洁、语气客观专业、描述详精练抓重点
 - 绝对不允许使用除代码框外的markdown语法（**、*、`、#、-等符号）
 - 如果需要给出代码, 请添加到代码框内, 只给出部分代码即可, 尽可能减少回复字数
 - 减少"根据搜索结果"、"未发现相关信息"等无意义表述
 - 由于搜索客观实效性, 避免 `预计` `大概` `可能` 等词汇
 - 不能使用 `教程` `怎么办` 等词汇进行搜索, 这些词汇会导致搜索结果偏离主题, 而且非官方信息居多
 - 回复带有紧扣结果相关的「补充推测」, 帮助用户进行下一步行动
 
 [推测]
 - 回复推测时也要使得语气平稳、陈述
 - 回复推测语句简短, 给出3种风格接近的「回复推测」与1种风格不同但仍主题相关的「回复推测」, 通常在10个字左右
 - 一些合适的推测示例方向: /1 深入研究 /2 了解更多关于 /3 继续深度搜索 /4 解决方案 /1 官方文档的最佳实践 /2 给出实际代码片段 ...
 
 [安全审查]
 - 禁止讨论政策、国家领导人、政治体制等敏感话题的搜索与验证计划、新闻、历史事件
 - 过于敏感的话题, 规划时请谨慎
 - 过于色情、暴力、血腥等内容, 请谨慎处理, 避免直接描述

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

    async def compress_text(self, text):
        """Compress text content using AI"""
        if not self.compress_content_enabled or not text:
            return text
        
        start_time = time.time()
        try:
            logger.info(f"开始压缩内容 - 原始长度: {len(text)}")
            messages = [
                {"role": "system", "content": self.compress_expert_system_prompt},
                {"role": "user", "content": f"请压缩以下网页内容:\n\n{text}"}
            ]
            
            resp = await self.compress_client.chat.completions.create(
                model=self.compress_model_name,
                messages=messages
            )
            compressed_text = resp.choices[0].message.content
            elapsed = time.time() - start_time
            logger.info(f"内容压缩完成 - 耗时: {elapsed:.2f}s, 压缩后长度: {len(compressed_text)}")
            return f"[已压缩 - 耗时{elapsed:.2f}s]\n{compressed_text}"
        except Exception as e:
            logger.error(f"内容压缩失败: {e}")
            return text

    async def browser_navigate(self, url):
        """Navigate to a URL and return the page content"""
        content = ""
        if self.use_jina:
            content = await self._navigate_jina(url)
        else:
            content = await self._navigate_playwright(url)
            
        if self.compress_content_enabled and not content.startswith("Error"):
             content = await self.compress_text(content)
             
        return content

    async def _navigate_jina(self, url):
        """Navigate using Jina AI"""
        try:
            logger.info(f"Jina AI navigating to: {url}")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"https://r.jina.ai/{url}")
                if resp.status_code == 200:
                    content = resp.text
                    logger.info(f"Successfully fetched {len(content)} chars from {url} via Jina")
                    return content
                else:
                    return f"Error navigating to {url} via Jina: Status {resp.status_code}"
        except Exception as e:
            logger.error(f"Jina navigation failed: {e}")
            return f"Error navigating to {url} via Jina: {str(e)}"

    async def _navigate_playwright(self, url):
        """Navigate using Playwright with a fresh context/page"""
        await self._ensure_browser()
        
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
            html = await page.content()
            
            # Use trafilatura for extraction
            content = trafilatura.extract(
                html,
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

    async def analyze_images(self, images: list[str]) -> str:
        """Analyze images and return description"""
        if not images:
            return ""
            
        try:
            logger.info(f"开始图片分析 - 图片数量: {len(images)}")
            
            system_prompt = self.vision_expert_system_prompt
            
            img_content: list[dict] = [{'type': 'text', 'text': '请分析这些图片'}]
            for img in images:
                img_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}})

            img_messages = [
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": img_content
                }
            ]
            img_resp = await self.vision_client.chat.completions.create(
                model=self.vision_model_name,
                messages=img_messages
            )
            if img_resp.choices[0].message.content:
                logger.info(f"图片分析完成")
                return img_resp.choices[0].message.content
        except Exception as e:
            logger.error(f"图片分析失败: {e}")
            return ""
        return ""

    async def call_tool(self, tool_call):
        func_name = tool_call.function.name
        # Decode HTML entities in arguments before parsing
        args_str = html.unescape(tool_call.function.arguments)
        args = json.loads(args_str)
        if func_name == "browser_navigate":
            url = args.get("url")
            return await self.browser_navigate(url)
        return f"Error: Unknown tool {func_name}"

    def _tool_msg(self, tool_call_id, content, is_error=False, elapsed_time=None):
        msg_content = f"错误: {content}" if is_error else str(content)
        if elapsed_time is not None:
            msg_content = f"[已运行: {elapsed_time:.2f}s] {msg_content}"
        return {
            "role": "tool", 
            "tool_call_id": tool_call_id, 
            "content": msg_content
        }

    async def _run_tool_isolated(self, tool_call, agent_start_time):
        tool_start = time.time()
        try:
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
        finally:
            pass

    def _format_extra_content(self, content):
        """Format extra content like reasoning or annotations"""
        if isinstance(content, (list, dict)):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)

    async def agent(self, user_input, conversation_history=None, browser_session=None, sonar_result=None, images=None):
        start_time = time.time()
        
        # Vision/OCR Analysis
        image_analysis = ""
        if images:
            image_analysis = await self.analyze_images(images)
        
        messages: list[dict] = [{"role": "system", "content": self.base_system_prompt}]
        
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
                        resp = await self.client.chat.completions.create(
                            model=self.model_name, 
                            messages=messages, 
                            tools=self.tools, 
                            tool_choice="auto",
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
                            "conversation_history": messages
                        }
                    return {"llm_response": "Error: Failed to get response from LLM", "conversation_history": messages}

                msg = resp.choices[0].message
                # Convert to dict to ensure tool_calls are preserved
                msg_dict = msg.model_dump(exclude_none=True)
                
                # Process reasoning and annotations
                annotations = msg_dict.get('annotations')
                
                # Remove reasoning_details and annotations to avoid decryption/citation errors
                msg_dict.pop('reasoning_details', None)
                msg_dict.pop('annotations', None)
                msg_dict.pop('reasoning', None)
                
                # Add system message with search info if available (for future context)
                if annotations:
                    # Limit annotations to top 3 to avoid excessive context
                    # OpenRouter :online defaults to 5, which can be too verbose
                    limited_annotations = annotations[:3]
                    search_info = self._format_extra_content(limited_annotations)
                    system_msg = {
                        "role": "system", 
                        "content": f"[搜索结果/引用来源]\n{search_info}"
                    }
                    # Insert system message BEFORE the assistant's response
                    messages.append(system_msg)
                
                messages.append(msg_dict)
                
                logger.info(f"LLM Response: content={bool(msg.content)}, tools={bool(msg.tool_calls)}")

                if msg.tool_calls:
                    tasks = [self._run_tool_isolated(tc, start_time) for tc in msg.tool_calls]
                    results = await asyncio.gather(*tasks)
                    messages.extend(results)
                elif msg.content:
                    logger.success("Conversation completed")
                    # Save conversation history to JSON file for debugging
                    self._save_conversation_debug(messages)
                    # Filter conversation_history: remove system messages but keep all tool-related messages
                    filtered_history = [m for m in messages if m.get("role") != "system"]
                    return {"llm_response": msg.content, "conversation_history": filtered_history}
            
            # Save conversation history even if max turns reached
            self._save_conversation_debug(messages)
            return {"llm_response": "Max turns reached", "conversation_history": messages}
        finally:
            pass
    
    def _save_conversation_debug(self, messages):
        """Save conversation history to JSON file for debugging"""
        if not self.debug:
            return
        
        try:
            import os
            debug_dir = "conversation_debug"
            os.makedirs(debug_dir, exist_ok=True)
            

            timestamp = int(time.time())
            filename = os.path.join(debug_dir, f"conversation_{timestamp}.json")
            
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"Conversation saved to {filename}")
        except Exception as e:
            logger.warning(f"Failed to save conversation debug: {e}")

    async def _ensure_browser(self):
        """Ensure Playwright browser is initialized"""
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        
        if self.browser is None:
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"]
            )
            logger.info("Playwright browser initialized")
