import asyncio
import json
import time
import trafilatura
from typing import Optional
from openai import AsyncOpenAI
from playwright.async_api import async_playwright
from loguru import logger

class HYW:
    def __init__(self, api_key, model_name, base_url, search_engine, headless=False, debug=False, 
                 vision_model_name=None, vision_base_url=None, vision_api_key=None,
                 ocr_model_name=None, ocr_base_url=None, ocr_api_key=None):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name
        self.search_engine = search_engine
        self.headless = headless
        self.debug = debug
        
        # Vision analysis configuration
        self.vision_model_name = vision_model_name if vision_model_name else model_name
        self.vision_base_url = vision_base_url if vision_base_url else base_url
        self.vision_api_key = vision_api_key if vision_api_key else api_key
        
        # OCR configuration
        self.ocr_model_name = ocr_model_name if ocr_model_name else self.vision_model_name
        self.ocr_base_url = ocr_base_url if ocr_base_url else self.vision_base_url
        self.ocr_api_key = ocr_api_key if ocr_api_key else self.vision_api_key

        # Global browser instance
        self._playwright = None
        self._browser = None

        # Create separate client if custom base_url or api_key is specified for vision
        if vision_base_url or vision_api_key:
            self.vision_client = AsyncOpenAI(
                base_url=self.vision_base_url,
                api_key=self.vision_api_key
            )
            logger.info(f"视觉分析客户端已创建 - 端点: {self.vision_base_url}, 模型: {self.vision_model_name}")
        else:
            # Reuse main client when using same base_url
            self.vision_client = self.client
            if vision_model_name:
                logger.info(f"视觉分析使用主客户端 - 模型: {self.vision_model_name}")
            else:
                logger.info(f"视觉分析使用主客户端和主模型: {self.vision_model_name}")
        
        # Create OCR client
        if ocr_base_url or ocr_api_key or (ocr_model_name and ocr_model_name != self.vision_model_name):
             self.ocr_client = AsyncOpenAI(
                base_url=self.ocr_base_url,
                api_key=self.ocr_api_key
            )
             logger.info(f"OCR客户端已创建 - 端点: {self.ocr_base_url}, 模型: {self.ocr_model_name}")
        else:
            self.ocr_client = self.vision_client
            logger.info(f"OCR使用视觉客户端 - 模型: {self.ocr_model_name}")
        
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
            },
        ]
        
        self.tools_desc = "\n".join([f"- {t['function']['name']}" for t in self.tools])
        
        self.vision_expert_system_prompt = """你是一个专业的图像分析专家。

 [核心任务]
 - 你是一个灵活的视觉专家模型，你分析出来的内容将交给接下来的文本分析专家作为参考.
 - 请总结性描述图片的内容，提取其中的关键信息（如文字、物体、场景、人物等），以便后续进行搜索和验证.
 - 在总结的过程中可以列出图片中在一些补充关键点.

 [文本]
 - 如果图片中包含任何文本内容（包括但不限于：标题、正文、标签、按钮文字、水印、字幕等），必须将所有文本内容完整、准确地转录出来
 - 文本转录要求：逐字逐句，不要遗漏、不要概括、不要改写
 - 对于多种语言的文本，都需要完整转录
 - 标注文本的位置和上下文关系（例如：图片顶部的标题、按钮上的文字、水印等）

 [输出格式]
 - 直接输出描述和文本内容，不要包含其他废话
 - 如有文本，请在描述开头优先列出：[图片文本内容] 然后列举所有文本
 - 随后描述图片的其他视觉元素

 [安全审查]
 - 如果图片包含敏感内容，尽量含糊回避, 不直接描述敏感内容
"""
        
        self.ocr_expert_system_prompt = """你是一个OCR专家。
[任务]
- 准确识别图片中的所有文字。
- 按自然阅读顺序输出。
- 不要包含任何描述性语言，只输出识别到的文字。
- 如果没有文字，输出"无文字"。
"""

        self.base_system_prompt = f"""你是一个搜索和信息验证AI助手, 你的目的是从用户的给出的信息中提取关键词并加以解释说明, 帮助用户完成使用解释的方式完成问题.
你拥有强大的思维链能力，在回答前请先进行深度的思考和隐形规划。

 [首选搜索引擎]
 {self.search_engine}
 
 [使用以下工具来搜索和验证信息]
 {self.tools_desc}

 [任务分析与规划]
 - 收到请求后，首先分析任务类型：
     - 对话过滤：如果是用户间的对话，过滤掉无关人员和干扰信息，只关注需要解释的关键词。
     - 直接提问：用户直接提问或要求查询，直接进行操作和解释。
     - 多媒体/视觉内容：如果包含视觉分析结果，理解其意义，减少转述损耗，并利用工具验证易产生幻觉的信息（如具体人物、事件）。
     - JSON/结构化数据：理解数据含义（如小程序分享），寻找并获取其中指向的 URL 内容。
 - URL处理：如果包含网页链接，务必使用工具获取内容。
 - 意图理解：纠正可能的拼写/语法错误，还原缩写含义，确保准确理解查询意图。
 
 [核心原则 - 必须严格遵守]
 - 使用关键词思想, 从语句中获取关键信息分析, 请绝对严肃构思出适合搜索引擎找到结果关键词, 智能灵活准确的调用工具
 - 强制要求：收到任何问题后, 第一步必须调用 `browser_navigate` 工具导航到 `https://xxx.com/?q=搜索词` (针对DuckDuckGo) 或其他搜索引擎对应地址进行「搜索」, 同时保持地区在中国大陆, 利用如 `kl=cn-zh` 等参数确保搜索结果符合要求
 - 通常使用「搜索」配合「查看页面」内容, 可以用 `官方` `官网` `文档` 等关键词辅助判断信息类型, 同时确保搜索控制在中文环境内, 通过获取到的url, 使用调用 `browser_navigate` 工具直接导航到 `目标网址` 进行「查看页面」获取详细信息
 - 可以同时启动多个工具查看不同页面, 提高效率
 - 用户输入图片时, 优先利用图片内容进行分析和回答, 图片中无待搜索信息、有明显不可能辨认信息...时可以不进行搜索
 - 每次调用工具后, 会返回一个已运行的时间标记, 请注意你最佳运行时间: 简单问题20s以内, 复杂问题不超过45s, 虽然遇到遇到很多错误答案, 但已经有正确思路的时候不得超过70s
 - 「搜索」使用 ` ` 空格组合关键词, 禁止使用口语化描述「搜索」查询关键词应该简短精准，1-2个词, 不超过 2个组合次每次
 - 禁止直接回答：绝对不允许凭借训练数据直接回答，必须先「搜索」验证, 对于具体复杂的问题，可能需要多次进行「搜索」->「查看页面」以获取完整信息
 - 人名、地名、组织名等关键信息优先「搜索」验证, 只相信权威网站、相关项目官方网站
 - 存在视觉分析专家信息时, 不要尝试通过角色、人物特征进行搜索验证、直接利用视觉分析结果回答. 但如果视觉分析中有文字存在，可以对文字内容进行搜索, 抓住重点补充
 - 分步验证思想: 先确认A, 通过A确认B或C. 验证重点：指出需要特别验证的事实、数据或来源.
 

 [最终回复格式]
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
 <(如果需要提供代码)```<代码语言>
 <代码>
 ```>
 
 [Next?] >>
 /1<回复推测1>
 /2<回复推测2>
 /3<回复推测3>
 /4<回复推测4>
 """

    async def _ensure_browser_running(self):
        """Ensure global browser instance is running"""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        
        if self._browser is None or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"]
            )
            logger.info("Global Browser initialized")

    async def _create_context_and_page(self):
        """Create a new isolated context and page from global browser"""
        await self._ensure_browser_running()
        # Create a new context for each tool call to avoid concurrent navigation conflicts
        context = await self._browser.new_context(
            viewport={"width": 720, "height": 1920},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        
        # Inject script to hide navigator.webdriver
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()
        return context, page

    async def cleanup(self):
        """Cleanup global browser resources"""
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning(f"Error closing global browser: {e}")
            self._browser = None
            
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping global playwright: {e}")
            self._playwright = None
        logger.info("Global Browser resources cleaned up")

    async def start_browser(self):
        """Initialize Playwright browser and return a page object."""
        # Keep this for backward compatibility or standalone usage
        playwright = await async_playwright().start()
        # Use args to reduce bot detection
        browser = await playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"]
        )
        context = await browser.new_context(
            viewport={"width": 720, "height": 1920},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        
        # Inject script to hide navigator.webdriver
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()
        logger.info("Browser initialized successfully")
        return playwright, browser, context, page

    async def close_browser(self, playwright, browser, context):
        """Safely close browser resources"""
        if context:
            try:
                await context.close()
            except Exception as e:
                logger.warning(f"Error closing context: {e}")
        if browser:
            try:
                await browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
        if playwright:
            try:
                await playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
        logger.info("Browser closed")

    async def browser_navigate(self, page, url):
        """Native implementation of browser navigation"""
        if not page or page.is_closed():
            return "Error: Page is not available."

        try:
            logger.info(f"Navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Cap load event to 5 seconds, the page is operational at this point.
            try:
                await page.wait_for_load_state("load", timeout=5000)
            except Exception:
                pass

            # Get page content using trafilatura for better extraction
            html = await page.content()
            content = trafilatura.extract(
                html,
                include_links=True,
                include_tables=True,
                output_format="markdown",
                favor_recall=False
            )
            
            # Fallback to innerText if trafilatura fails
            if not content:
                content = await page.evaluate("() => document.body.innerText")
                
            return content
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return f"Error navigating to {url}: {str(e)}"

    async def browser_click(self, page, selector):
        """Click an element on the page"""
        if not page or page.is_closed():
            return "Error: Page is not available."
        
        try:
            logger.info(f"Clicking element: {selector}")
            await page.click(selector, timeout=5000)
            
            # Wait for potential navigation or load
            try:
                await page.wait_for_load_state("load", timeout=5000)
            except Exception:
                pass
                
            # Get page content
            html = await page.content()
            content = trafilatura.extract(
                html,
                include_links=True,
                include_tables=True,
                output_format="markdown",
                favor_recall=False
            )
            
            if not content:
                content = await page.evaluate("() => document.body.innerText")
                
            return content
        except Exception as e:
            logger.error(f"Click failed: {e}")
            return f"Error clicking {selector}: {str(e)}"

    async def call_tool(self, page, tool_call):
        func_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        if func_name == "browser_navigate":
            return await self.browser_navigate(page, args.get("url"))
        elif func_name == "browser_click":
            return await self.browser_click(page, args.get("selector"))
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

    async def analyze_images(self, images: list[str], use_ocr: bool = False) -> str:
        """Analyze images and return description"""
        if not images:
            return ""
            
        try:
            logger.info(f"开始视觉分析 - 图片数量: {len(images)}, OCR模式: {use_ocr}")
            
            img_content: list[dict] = [{'type': 'text', 'text': '请分析这些图片'}]
            for img in images:
                img_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}})

            img_messages = [
                {
                    "role": "system", 
                    "content": self.ocr_expert_system_prompt if use_ocr else self.vision_expert_system_prompt
                },
                {
                    "role": "user",
                    "content": img_content
                }
            ]
            
            client = self.ocr_client if use_ocr else self.vision_client
            model = self.ocr_model_name if use_ocr else self.vision_model_name
            
            img_resp = await client.chat.completions.create(
                model=model,
                messages=img_messages
            )
            if img_resp.choices[0].message.content:
                logger.info("视觉分析完成")
                return img_resp.choices[0].message.content
        except Exception as e:
            logger.error(f"视觉分析失败: {e}")
            return ""
        return ""


    async def _run_tool_isolated(self, tool_call):
        process_start = time.time()
        # Create an isolated context for this tool call
        context, page = await self._create_context_and_page()
        try:
            start = time.time()
            try:
                result = await self.call_tool(page, tool_call)
                elapsed = time.time() - start
                total_elapsed = time.time() - process_start
                logger.info(f"Tool {tool_call.function.name} finished in {elapsed:.2f}s (Total: {total_elapsed:.2f}s)")
                return self._tool_msg(tool_call.id, result, elapsed_time=elapsed)
            except Exception as e:
                elapsed = time.time() - start
                logger.error(f"Tool failed: {e}")
                return self._tool_msg(tool_call.id, e, is_error=True, elapsed_time=elapsed)
        finally:
            # Close the entire context to clean up resources
            await context.close()

    async def agent(self, user_input, conversation_history=None, browser_session=None, image_analysis=None):
        start_time = time.time()
        
        messages: list[dict] = [{"role": "system", "content": self.base_system_prompt}]
        
        if image_analysis:
            messages.append({"role": "system", "content": f"[视觉专家分析报告]\\n{image_analysis}"})

        if conversation_history:
            messages.extend([m for m in conversation_history if m.get("role") != "system"])
        
        messages.append({"role": "user", "content": user_input})

        logger.info(f"Processing: {user_input[:50]}...")
        
        # Use provided browser session if available
        playwright = browser = context = page = None
        if browser_session and browser_session[3]:
            playwright, browser, context, page = browser_session
        
        try:
            for _ in range(25):
                resp = await self.client.chat.completions.create(
                    model=self.model_name, 
                    messages=messages, 
                    tools=self.tools, 
                    tool_choice="auto",
                )
                msg = resp.choices[0].message
                # Convert to dict to ensure tool_calls are preserved
                msg_dict = msg.model_dump(exclude_none=True)
                messages.append(msg_dict)
                
                logger.info(f"LLM Response: content={bool(msg.content)}, tools={bool(msg.tool_calls)}")

                if msg.tool_calls:
                    if page:
                        for tc in msg.tool_calls:
                            start = time.time()
                            try:
                                result = await self.call_tool(page, tc)
                                logger.info(f"Tool {tc.function.name} finished in {time.time() - start:.2f}s")
                                messages.append(self._tool_msg(tc.id, result, elapsed_time=time.time() - start))
                            except Exception as e:
                                logger.error(f"Tool failed: {e}")
                                messages.append(self._tool_msg(tc.id, e, is_error=True, elapsed_time=time.time() - start))
                    else:
                        tasks = [self._run_tool_isolated(tc) for tc in msg.tool_calls]
                        results = await asyncio.gather(*tasks)
                        messages.extend(results)
                elif msg.content:
                    logger.success("Conversation completed")
                    # Save conversation history to JSON file for debugging
                    self._save_conversation_debug(messages)
                    return {"llm_response": msg.content, "conversation_history": [m for m in messages if m.get("role") != "system"]}
            
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
