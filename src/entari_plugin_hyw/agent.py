import asyncio
import base64
import httpx
import json
from typing import Any, List, Optional, Union, Callable
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from arclet.entari import BasicConfModel, metadata, plugin_config

from langchain_core.messages import AIMessage, BaseMessage
from typing import List as TypingList
    
from pydantic import SecretStr
from satori.exception import ActionFailed
from arclet.entari import BasicConfModel
from loguru import logger
import urllib.parse


class HywConfig(BasicConfModel):
    hyw_command_name: Union[str, List[str]] = "hyw"
    
    # AI配置 - 必需字段，无默认值
    text_llm_model_name: str
    text_llm_api_key: str
    text_llm_model_base_url: str
    text_llm_temperature: float = 0.4
    text_llm_enable_search: bool = False
    
    vision_llm_model_name: str
    vision_llm_api_key: str
    vision_llm_model_base_url: str
    vision_llm_temperature: float = 0.4
    vision_llm_enable_search: bool = False

    search_engine: str = "bing"


_global_search_engine = "bing"

def set_global_search_engine(engine: str):
    """设置全局搜索引擎"""
    global _global_search_engine
    _global_search_engine = engine

# 智能搜索工具
@tool
async def smart_search(queries: List[str]) -> str:
    """
    智能搜索工具，自动判断查询类型并选择最适合的搜索方式, 返回JSON格式文本结果
    queries: 搜索关键词列表
    """

    async def ddsg_search(keyword: str, backend: str) -> List[dict]:
        """文本搜索"""
        from ddgs import DDGS
        
        def _sync_search():
            """同步搜索函数，在线程池中执行"""
            logger.info(f"使用 DDGS 搜索: 关键词='{keyword}', 引擎='{backend}'")
            return DDGS().text(
                keyword,
                safesearch="off",
                timelimit="y",
                page=1,
                backend=backend,
            )
        
        # 使用 asyncio.to_thread 在线程池中运行同步操作，不阻塞事件循环
        results = await asyncio.to_thread(_sync_search)
        return results

    try:
        # 将查询分为精确搜索和一般搜索两组
        exact_queries = []
        web_queries = []
        
        for query in queries:
            if ' ' not in query:
                exact_queries.append(f'"{query}"')
                logger.info(f"'{query}' 判定为精确搜索")
            else:
                web_queries.append(query)
                logger.info(f"'{query}' 判定为一般搜索")
        
        # 执行搜索 - 并发执行所有搜索任务
        async def search_task(query: str, backend: str, search_type: str):
            """搜索任务"""
            results = await ddsg_search(query, backend=backend)
            return {
                "query": query,
                "results": results,
                "search_type": search_type
            }
        
        # 构建所有搜索任务 - 同时构建精确搜索和一般搜索任务
        search_tasks = (
            [search_task(query, backend=_global_search_engine, search_type="exact") for query in exact_queries] +
            [search_task(query, backend=_global_search_engine, search_type="web") for query in web_queries]
        )
        
        # 并发执行所有搜索任务
        all_results = await asyncio.gather(*search_tasks) if search_tasks else []
    
        
        logger.info(f"智能搜索结果: {all_results}")
        return json.dumps(all_results, ensure_ascii=False, indent=2)
        
    except Exception as e:
        # 错误处理
        error_results = [
            {
                "query": query,
                "results": [],
                "search_type": "unknown",
                "error": str(e)
            }
            for query in queries
        ]
        logger.error(f"智能搜索失败: {e}")
        return json.dumps(error_results, ensure_ascii=False, indent=2)


@tool
async def jina_fetch_webpage(url: str) -> str:
    """
    输入网址, 获取网页内容
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"https://r.jina.ai/{url}")
            if resp.status_code == 200:
                # logger.info(f"网页获取成功 {resp.text}")
                return resp.text
            else:
                return f"获取网页失败，状态码: {resp.status_code}"
    except Exception as e:
        return f"获取网页失败: {str(e)}"


@tool
async def nbnhhsh(text: str) -> str:
    """
    用于复原一个缩写的所有可能性
    注意: 此工具会缩写推演所有可能性, 客观存在很多污染
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("https://lab.magiconch.com/api/nbnhhsh/guess", json={"text": text})
            if resp.status_code == 200:
                _res =  json.dumps(resp.json(), ensure_ascii=False, indent=2)
                logger.info(f"nbnhhsh 解释成功: {_res}")
                return _res
            else:
                return f"API请求失败: {resp.status_code}"
    except Exception as e:
        return f"解释失败: {str(e)}"

# 添加视觉专家工具
async def _vision_expert_analysis(vision_llm: ChatOpenAI, image_data: bytes, query: str = "") -> str:
    """视觉专家分析工具"""
    logger.info("调用视觉专家分析")
    try:
        img_data = base64.b64encode(image_data).decode()
        message_content = [
            {"type": "text", "text": f"请分析这张图片内容。用户问题：{query}" if query else "请详细分析这张图片的内容"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}}
        ]
        
        vision_prompt = """你是视觉分析专家，请分析图片内容：

- 一大段话详尽的描述主要内容, 
- 如果出现文字, 请给出所有文字内容

输出格式：
第一张图描述了... 此外...
...
"""
        
        result = await vision_llm.ainvoke([
            SystemMessage(content=vision_prompt),
            HumanMessage(content=message_content)
        ])
        logger.info(f"视觉专家分析完成: {result}")
        return str(result.content) if hasattr(result, 'content') else str(result)
    except Exception as e:
        return f"视觉分析失败: {str(e)}"


class AgentService:
    """AI服务类，管理文本和视觉LLM"""
    
    def __init__(self, config: "HywConfig"):
        self.config = config
        self._text_llm: Optional[ChatOpenAI] = None
        self._vision_llm: Optional[ChatOpenAI] = None
        self._planning_agent: Optional[Any] = None
        self._search_tool_name: Optional[str] = None  # 保存动态生成的搜索工具名称
        self._compressor_llm: Optional[ChatOpenAI] = None  # 用于压缩内容的LLM
        
        # 设置全局搜索引擎配置
        self._set_search_engine()
        self._init_models()
        self._init_agents()
    
    def _set_search_engine(self):
        """设置搜索引擎配置"""
        set_global_search_engine(self.config.search_engine)

    
    def _init_models(self):
        """初始化LLM模型"""
        self._text_llm = ChatOpenAI(
            model=self.config.text_llm_model_name,
            api_key=SecretStr(self.config.text_llm_api_key),
            base_url=self.config.text_llm_model_base_url,
            temperature=self.config.text_llm_temperature,
            extra_body={"enable_search": self.config.text_llm_enable_search}
        )
        
        self._vision_llm = ChatOpenAI(
            model=self.config.vision_llm_model_name,
            api_key=SecretStr(self.config.vision_llm_api_key),
            base_url=self.config.vision_llm_model_base_url,
            temperature=self.config.vision_llm_temperature,
            extra_body={"enable_search": self.config.vision_llm_enable_search}
        )
        
        # 初始化压缩器LLM - 使用较低温度保证压缩质量
        self._compressor_llm = ChatOpenAI(
            model=self.config.text_llm_model_name,
            api_key=SecretStr(self.config.text_llm_api_key),
            base_url=self.config.text_llm_model_base_url,
            temperature=0.1,  # 低温度，保证压缩的一致性和准确性
            extra_body={"enable_search": False}
        )
    
    def _init_agents(self):
        """初始化专家Agent系统"""
        if self._text_llm is None:
            self._planning_agent = None
            return
        
        # 创建规划专家（判断专家）
        planning_llm = ChatOpenAI(
            model=self.config.text_llm_model_name,
            api_key=SecretStr(self.config.text_llm_api_key),
            base_url=self.config.text_llm_model_base_url,
            temperature=0.2,  # 较低温度，保持规划的一致性
            extra_body={"enable_search": False}
        )
        
        # 设置搜索工具名称
        self._search_tool_name = "smart_search"
        
        # 为规划专家绑定所有工具，使用新的智能搜索工具
        all_tools = [smart_search, jina_fetch_webpage, nbnhhsh]
        self._planning_agent = planning_llm.bind_tools(all_tools)

    @staticmethod
    async def download_image(url: str) -> bytes:
        """下载图片"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
                else:
                    raise ActionFailed(f"下载图片失败，状态码: {resp.status_code}")
        except Exception as e:
            raise ActionFailed(f"下载图片失败: {url}, 错误: {str(e)}")
    
    async def _compress_webpage(self, webpage_content: str, url: str) -> str:
        """使用LLM压缩网页内容"""
        if self._compressor_llm is None:
            raise RuntimeError("压缩器LLM未初始化")
        
        logger.info(f"[压缩器] 开始压缩网页内容，原始长度: {len(webpage_content)} 字符")
        logger.debug(f"[压缩器] 网页URL: {url}")
        
        compress_prompt = f"""你是网页内容提取专家，需要从网页中提取核心信息。

[任务]
从以下网页内容中提取主要信息，去除导航、广告、样板文字等噪音。

[网页URL]
{url}

[网页内容]
{webpage_content[:10000]}  # 限制输入长度

[提取要求]
1. 提取网页的主要内容和核心信息
2. 保留关键段落、标题、列表
3. 去除导航栏、页脚、广告、社交媒体按钮等
4. 保留重要的数据、日期、引用
5. 如果是新闻/文章，提取标题、作者、发布时间、正文摘要
6. 如果是百科，提取定义、关键属性、分类信息
7. 保持信息的逻辑结构

[输出格式]
网页: {url}
---
<压缩后的核心内容>
"""
        
        logger.info(f"[压缩器] 调用LLM压缩，prompt长度: {len(compress_prompt)} 字符")
        result = await self._compressor_llm.ainvoke([
            SystemMessage(content=compress_prompt)
        ])
        
        compressed = str(result.content) if hasattr(result, 'content') else str(result)
        logger.info(f"[压缩器] 压缩完成: {len(webpage_content)} -> {len(compressed)} 字符 (压缩率: {100 - len(compressed)*100//len(webpage_content)}%)")
        logger.debug(f"[压缩器] 压缩结果预览: {compressed[:300]}...")
        return compressed


    
    
    async def unified_completion(self, content: str, images: Optional[List[bytes]] = None, react_func: Optional[Callable[[str], Any]] = None) -> Any:
        """统一入口 - 使用 LangChain 自动工具执行"""
        if self._planning_agent is None:
            raise RuntimeError("规划专家未初始化")
        
        # 开始计时 - 包含整个处理流程
        import time
        total_start_time = time.time()
        
        # 收集专家信息和使用的模型
        expert_info = []
        model_names = self.config.text_llm_model_name
        
        # 1. 如果有图片，先调用视觉专家进行分析
        vision_time = 0.0
        if images:
            if react_func:
                await react_func("127847")  # 🍧 图片分析中
            model_names += f"[{self.config.vision_llm_model_name}]"
            
            vision_start_time = time.time()
            for i, image_data in enumerate(images):
                if self._vision_llm is None:
                    vision_result = "视觉专家不可用"
                else:
                    vision_result = await _vision_expert_analysis(self._vision_llm, image_data, content)
                expert_info.append(f"视觉专家分析{i+1}: {vision_result}")
            vision_time = time.time() - vision_start_time
            if react_func:
                await react_func("10024")  # ✨ 开始智能规划
            full_context = "\n".join([f"图片{i+1}分析结果: {res}" for i, res in enumerate(expert_info)]) + f"\n对话携带信息: {content}"
            logger.info("content:", content)
        else:
            if react_func:
                await react_func("10024")  # ✨ 开始智能规划
            
            # 2. 构建完整上下文
            context_parts = [f"文本信息: {content}"]
            if expert_info:
                context_parts.extend(expert_info)
            
            full_context = "\n".join(context_parts)
        
        # 3. 使用 LangChain 自动工具执行机制
        planning_prompt = f"""你是一个大语言模型驱动的智能解释器, 需要使用工具获取准确信息, 回答用户的问题.

[任务类型]
- 这是一句用户之间的对话, 我需要你去要从中过滤掉无关人员之间的对话信息 如人名与可能的上下文产物, 解释某一个用户对这句话中不理解的关键词
    - 我需要排除对话间的干扰信息
    - 我需要详细多次使用工具获取信息来支持和增强回答的质量
- 用户在向我提问这句话、或希望我查询一些东西, 完成操作进行解释
    - 我需要详细多次使用工具获取信息来支持和增强回答的质量
- 这是一张视觉专家分析后的多媒体内容, 我需要理解其中的意义并进行解释这张图片:
    - 我需要减少转述损耗, 尽可能把视觉专家的分析内容完整的传达给用户
    - 同时我需要利用工具确认、获取、验证一些具体人物、角色、事件等大语言模型易产生幻觉的信息
- 如果携带信息包含网页链接 URL 、或潜在可以导向网站, 一定要使用工具查找和获取相关网页, 使用 jina_fetch_webpage 获取网页内容
- 给出的消息可能的拼写错误或语法错误, 以确保准确理解查询意图, 但确保不改变原意.

[信息渠道]
- smart_search
    - 先使用 smart_search 工具获取准确知识, 在进行回复
    - 必须先使用 smart_search 工具获取准确知识后再进行回复，严禁凭借训练数据直接回答
    - 可以构建不同组合、精细度、具体性的关键词列表进行二次搜索, 以获取更全面的信息
    - smart_search 推荐直接传入待查询内容, 如 ["Python", "python缩进"]
    - 请严格构建查询关键词列表, 请勿擅自分割用户的查询词语导致搜索引擎导向完全不同的结果
    - smart_search 不推荐如何口语化表达 如 ["xxx是什么意思" "xxx是什么"]
- jina_fetch_webpage
    - 如果用户给出类似链接 网址 URL 或潜在能找到网址的内容时，一定要使用工具查找和获取相关网页, 使用 jina_fetch_webpage 获取网页内容, 仔细分析网页内容以补充回答
- 优先使用官方可靠的信息源：
    - 维基百科、萌娘百科等百科网站（信息相对准确）
    - 官方网站、官方Wiki（最权威）
    - 多个独立来源的一致信息（交叉验证）
- 构建精巧的分布搜索计划：
    - 第一轮：搜索主要关键词，获取基本信息
    - 第二轮：针对关键属性（如"XX 所属"、"XX 隶属组织"）进行精确搜索
    - 第三轮：交叉验证，确认信息一致性
- 禁止使用噪音信息源：
    - 大部分商业网站 视频网站 小红书等等类似网站充满大量噪音和无效信息，通常不适合使用 jina_fetch_webpage 获取内容


[最终回复]
- [回答原则]
    - 必须使用智能搜索工具获取最新和准确的信息，绝对不允许依赖训练数据
    - 永远使用中文回答
    - 用客观 专业 准确的百科式语气回答问题
    - 当有视觉解释, 视觉内容解释优先度最高
    - 回答简短高效, 不表明自身立场, 专注客观回复
    - 不需要每个关键词都解释, 只解释用户最关心的关键词
    - 围绕结果可以展开一些拓展内容, 但不要偏离主题
    - 避免将不同项目的信息混合在一起描述

- [严格禁止的行为]
    - 不经过搜索验证直接回答用户问题
    - 进展在回复中添加任何未经工具验证的信息
    - 绝对不允许使用任何markdown语法, 包括但不限于: **加粗**, *斜体*, `代码`, # 标题, - 列表
    - 绝对不允许出现任何**或*符号用于强调或加粗, 这些符号在任何情况下都不应出现在回复中
    - 使用纯文本格式, 需要强调时使用「」或『』等中文符号
    - 绝对不允许说"并非一个通用技术术语或广为人知的...非广为人知的信息或通用技术术语" "根据搜索结果显示..." "目前未发现相关信息..."等无意义表述

- [格式要求]
    第一行: [Key] :: <关键词> | <关键词>  <...>
    第二行: >> [LLM Agent] :: {model_names}
    第三行开始: <详细解释>

[开始]
开始分析并执行！

[当前情况]
{full_context}

"""

        # 工具调用统计 - 在try外面初始化
        tool_stats = {}
        
        try:
            # 使用 LangChain 的消息循环自动执行工具
            messages: TypingList[BaseMessage] = [SystemMessage(content=planning_prompt)]
            logger.info(f"[开始对话] 初始化消息列表，prompt长度: {len(planning_prompt)} 字符")
            
            # 持续执行直到没有工具调用或达到最大轮次
            max_iterations = 10
            iteration = 0
            result = None
            
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"========== 第 {iteration} 轮迭代开始 ==========")
                
                # 如果是最后一轮（距离最大轮次还有1轮），添加提醒消息
                if iteration == max_iterations - 1:
                    reminder_message = SystemMessage(content="注意：这是最后一轮工具调用机会。请直接给出最终答案。")
                    messages.append(reminder_message)
                    logger.warning(f"[最后一轮] 添加提醒消息")
                
                # 计算当前上下文大小
                context_size = sum(len(str(m.content)) for m in messages if hasattr(m, 'content'))
                logger.info(f"[第 {iteration} 轮] 当前上下文大小: {context_size} 字符, 消息数: {len(messages)}")
                
                result = await self._planning_agent.ainvoke(messages)                
                # 将AI响应添加到消息历史
                messages.append(result)
                logger.info(f"[第 {iteration} 轮] AI响应已添加到消息历史")
                logger.debug(f"[第 {iteration} 轮] AI响应内容: {result}")
                
                # 检查是否有工具调用
                if hasattr(result, 'tool_calls') and result.tool_calls:
                    logger.info(f"执行工具调用: {[tc['name'] for tc in result.tool_calls]}")
                    
                    # 执行每个工具调用
                    for tool_call in result.tool_calls:
                        tool_name = tool_call['name']
                        tool_start_time = time.time()
                        
                        # 初始化工具统计
                        if tool_name not in tool_stats:
                            tool_stats[tool_name] = {'count': 0, 'total_time': 0}
                        
                        # 调用对应的工具（现在都是异步的）
                        if tool_name == self._search_tool_name:
                            logger.info(f"[工具调用] smart_search 参数: {tool_call['args']}")
                            tool_result = await smart_search.ainvoke(tool_call['args'])
                            logger.info(f"[结果] smart_search 返回长度: {len(tool_result)} 字符")
                                
                        elif tool_name == 'jina_fetch_webpage':
                            logger.info(f"[工具调用] jina_fetch_webpage 参数: {tool_call['args']}")
                            tool_result = await jina_fetch_webpage.ainvoke(tool_call['args'])
                            logger.info(f"[原始结果] jina_fetch_webpage 返回长度: {len(tool_result)} 字符")
                            
                            # 压缩网页内容
                            url = tool_call['args'].get('url', 'unknown')
                            original_len = len(tool_result)
                            tool_result = await self._compress_webpage(tool_result, url)
                            logger.info(f"[压缩完成] jina_fetch_webpage: {original_len} -> {len(tool_result)} 字符")
                            logger.debug(f"[压缩内容预览] {tool_result[:500]}...")
                                
                        elif tool_name == 'nbnhhsh':
                            logger.info(f"[工具调用] nbnhhsh 参数: {tool_call['args']}")
                            tool_result = await nbnhhsh.ainvoke(tool_call['args'])
                            logger.info(f"[结果] nbnhhsh 返回长度: {len(tool_result)} 字符")
                        else:
                            tool_result = f"未知工具: {tool_name}"
                            logger.error(f"[错误] 未知工具: {tool_name}")
                        
                        # 记录统计信息
                        tool_end_time = time.time()
                        tool_duration = tool_end_time - tool_start_time
                        tool_stats[tool_name]['count'] += 1
                        tool_stats[tool_name]['total_time'] += tool_duration
                        
                        # 将工具结果添加到消息历史
                        final_content = str(tool_result)
                        messages.append(ToolMessage(
                            content=final_content,
                            tool_call_id=tool_call['id']
                        ))
                        
                        logger.info(f"[添加到上下文] 工具 {tool_name} 结果长度: {len(final_content)} 字符，耗时 {tool_duration:.2f}s")
                        logger.info(f"[当前消息数] messages 总数: {len(messages)}")
                
                # 如果没有工具调用，说明模型自己决定停止并直接返回回答
                if not (hasattr(result, 'tool_calls') and result.tool_calls):
                    logger.info("没有更多工具调用，模型直接返回回答")
                    break
            
            # 计算总耗时
            total_duration = time.time() - total_start_time
            
            # 生成工具使用统计信息
            tool_stats_line = ""
            elapsed_parts = [f"total: {total_duration:.1f}s"]
            if vision_time > 0:
                elapsed_parts.append(f"vision: {vision_time:.1f}s")
            elapsed_line = f"[Elapsed] :: {' | '.join(elapsed_parts)}"
            
            if tool_stats:
                stats_parts = []
                for tool_name, stats in tool_stats.items():
                    # 如果是搜索工具，添加搜索引擎名称
                    if tool_name == 'smart_search':
                        display_name = f"smart_search[{self.config.search_engine}]"
                        stats_parts.append(f"{display_name}: {stats['count']}")
                    else:
                        stats_parts.append(f"{tool_name}: {stats['count']}")
                tool_stats_line = f"[Use Tools] :: {', '.join(stats_parts)}"
            
            # 检查最终结果
            if result and hasattr(result, 'content') and result.content:
                # 直接在原有内容后面添加统计信息
                original_content = result.content
                stats_parts = []
                if tool_stats_line:
                    stats_parts.append(tool_stats_line)
                stats_parts.append(elapsed_line)
                
                stats_text = "\n" + "\n".join(stats_parts)
                modified_content = original_content + stats_text
                return AIMessage(content=modified_content)
            else:
                stats_parts = []
                if tool_stats_line:
                    stats_parts.append(tool_stats_line)
                stats_parts.append(elapsed_line)
                
                stats_text = "\n" + "\n".join(stats_parts)
                fallback_content = f"[Key] :: 信息处理 | 处理异常\n>> [LLM Agent] :: {model_names}\n抱歉，暂时无法生成完整的回复内容。{stats_text}"
                return AIMessage(content=fallback_content)
                
        except Exception as e:
            logger.error(f"规划专家执行失败: {e}")
            
            # 计算总耗时（异常情况）
            total_duration = time.time() - total_start_time
            
            # 生成工具使用统计信息（异常情况）
            tool_stats_line = ""
            elapsed_parts = [f"total: {total_duration:.1f}s"]
            if vision_time > 0:
                elapsed_parts.append(f"vision: {vision_time:.1f}s")
            elapsed_line = f"[Elapsed] :: {' | '.join(elapsed_parts)}"
            
            if tool_stats:
                stats_parts = []
                for tool_name, stats in tool_stats.items():
                    # 如果是搜索工具，添加搜索引擎名称
                    if tool_name == 'smart_search':
                        display_name = f"smart_search[{self.config.search_engine}]"
                        stats_parts.append(f"{display_name}: {stats['count']}")
                    else:
                        stats_parts.append(f"{tool_name}: {stats['count']}")
                tool_stats_line = f"[Use Tools] :: {', '.join(stats_parts)}"
            
            stats_parts = []
            if tool_stats_line:
                stats_parts.append(tool_stats_line)
            stats_parts.append(elapsed_line)
            
            stats_text = "\n" + "\n".join(stats_parts)
            
            # 检查是否是内容审查失败
            error_msg = str(e)
            if "data_inspection_failed" in error_msg:
                fallback_content = f"[Key] :: 内容审查 | 审查失败\n>> [LLM Agent] :: {model_names}\n输入内容可能包含不当信息，无法处理。错误详情: {error_msg}\n{stats_text}"
            elif "inappropriate content" in error_msg.lower():
                fallback_content = f"[Key] :: 内容过滤 | 内容限制\n>> [LLM Agent] :: {model_names}\n内容被服务商过滤，无法生成回答。错误: {error_msg}\n{stats_text}"
            else:
                fallback_content = f"[Key] :: 系统异常 | 执行错误\n>> [LLM Agent] :: {model_names}\n系统处理异常: {error_msg}\n{stats_text}"
            
            return AIMessage(content=fallback_content)
