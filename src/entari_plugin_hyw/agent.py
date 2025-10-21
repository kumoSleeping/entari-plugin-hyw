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

from .utils import duck_search_real


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
    

# 智能搜索工具
@tool
async def smart_search(queries: List[str]) -> str:
    """智能搜索工具，自动判断查询类型并选择最适合的搜索方式, 返回JSON格式文本结果"""
    
    logger.info(f"智能搜索查询: {queries}")
    
    def is_exact_word(query: str) -> bool:
        """判断是否为需要精确搜索的词汇"""
        query = query.strip()
        
        # 如果包含空格，通常不是单词
        if ' ' in query:
            return False
        
        # 如果包含中文和英文混合，可能是特定术语
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in query)
        has_english = any('a' <= char.lower() <= 'z' for char in query)
        
        # 纯英文单词且长度适中，适合精确搜索
        if not has_chinese and has_english and 3 <= len(query) <= 20:
            return True
            
        # 看起来像专有名词 品牌名 技术术语等
        # 例如: bestdori, GitHub, API, Python等
        if not has_chinese and (query.islower() or query.isupper() or query.istitle()):
            return True
            
        # 包含数字和字母的组合，可能是版本号 型号等
        if any(char.isdigit() for char in query) and any(char.isalpha() for char in query):
            return True
            
        return False
    
    try:
        # 将查询分为精确搜索和一般搜索两组
        exact_queries = []
        web_queries = []
        
        for query in queries:
            if is_exact_word(query):
                exact_queries.append(query)
                logger.info(f"'{query}' 判定为精确搜索")
            else:
                web_queries.append(query)
                logger.info(f"'{query}' 判定为一般搜索")
        
        all_results = []
        
        # 执行精确搜索
        if exact_queries:
            logger.info(f"执行精确搜索: {exact_queries}")
            exact_results = await duck_search_real(
                keywords=exact_queries,
                max_results=5,
                region="zh-cn",
                exact_search=True
            )
            
            # 按关键词分组精确搜索结果
            exact_grouped = {}
            for result in exact_results:
                keyword = result.get('keyword', 'unknown')
                if keyword not in exact_grouped:
                    exact_grouped[keyword] = []
                exact_grouped[keyword].append(result)
            
            # 添加到最终结果
            for query in exact_queries:
                all_results.append({
                    "query": query,
                    "results": exact_grouped.get(query, []),
                    "search_type": "exact",
                    "error": None
                })
        
        # 执行一般搜索
        if web_queries:
            logger.info(f"执行一般搜索: {web_queries}")
            web_results = await duck_search_real(
                keywords=web_queries,
                max_results=5,
                region="zh-cn",
                exact_search=False
            )
            
            # 按关键词分组一般搜索结果
            web_grouped = {}
            for result in web_results:
                keyword = result.get('keyword', 'unknown')
                if keyword not in web_grouped:
                    web_grouped[keyword] = []
                web_grouped[keyword].append(result)
            
            # 添加到最终结果
            for query in web_queries:
                all_results.append({
                    "query": query,
                    "results": web_grouped.get(query, []),
                    "search_type": "web",
                    "error": None
                })
        
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
                logger.info(f"网页获取成功 {resp.text}")
                return resp.text
            else:
                return f"获取网页失败，状态码: {resp.status_code}"
    except Exception as e:
        return f"获取网页失败: {str(e)}"


# @tool
# async def nbnhhsh(text: str) -> str:
#     """
#     用于复原网络用语中的缩写
    
#     注意: 此工具客观存在很多污染

#     你只有以下两种情况需要使用此工具:
#     - 用户明确要求使用缩写查询
#     - 你已经使用 smart_search 等工具查询过该内容
#     - 此缩写过于毫无意义, 如 "xehd" "27djw" 等等
#     否则禁止使用此工具
#     """
#     try:
#         async with httpx.AsyncClient(timeout=15.0) as client:
#             resp = await client.post("https://lab.magiconch.com/api/nbnhhsh/guess", json={"text": text})
#             if resp.status_code == 200:
#                 return json.dumps(resp.json(), ensure_ascii=False, indent=2)
#             else:
#                 return f"API请求失败: {resp.status_code}"
#     except Exception as e:
#         return f"解释失败: {str(e)}"

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
- 如果用户有提出具体内容补充，同样作为描述主要内容的一部分

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
                
        self._init_models()
        self._init_agents()
    
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
        
        # 为规划专家绑定所有工具，使用新的智能搜索工具
        all_tools = [smart_search, jina_fetch_webpage]
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

[你需要智能识别以下场景, 做出不同的反应]
- 这是一句用户之间的对话, 我需要你去要从中过滤掉无关人员之间的对话信息 如人名与可能的上下文产物, 解释某一个用户对这句话中不理解的关键词
- 用户在向我提问这句话
- 用户希望我查询一些东西, 完成操作进行解释
- 这是一张视觉专家分析后的多媒体内容, 我需要理解其中的意义并进行解释这张图片
- 这是一张视觉专家分析后的多媒体内容, 我需要理解其中的意义并进行解释这张图片, 我需要减少转述损耗, 尽可能把视觉专家的分析内容完整的传达给用户

[关于智能决策使用工具]
- 先使用 smart_search 工具获取准确知识, 在进行回复
- smart_search 推荐每次搜索传入两种内容, 一种是关键词或专有名词等需要精确搜索的内容, 另一种是一般查询内容 例如: ["Python" "Python 学习 新手"]
- 如果用户给出类似链接 网址 URL 或潜在能找到网址的内容时，优先使用工具查找和获取相关网页, 使用 jina_fetch_webpage 获取网页内容, 仔细分析网页内容以补充回答
- 请智能决策应该专注于描述哪些内容
- 禁止为了噪音信息而调用工具
- 如果遇到非广为人知的词语或通用技术术语, 请一定使用搜索网络信息获取知识补充
- 大部分商业网站 视频网站 小红书等等类似网站充满大量噪音和无效信息，通常不适合使用 jina_fetch_webpage 获取内容

[使用工具]
尝试纠正用户可能的拼写错误或语法错误, 以确保准确理解查询意图, 但确保不改变原意.
一定要使用智能搜索工具获取最新和准确的信息, 补充回答内容的完整性和准确性。
精准多次使用工具获取的信息来支持和增强回答的质量，从工具中提取相关数据可以再次重组上下文, 继续调用相关工具得到更完整的答案。

[最终回复的回答原则]
- 永远使用中文回答
- 用客观 专业 准确的百科式语气回答问题
- 当有视觉解释, 视觉内容解释优先度最高
- 回答简短高效, 不表明自身立场, 专注客观回复
- 不需要每个关键词都解释, 只解释用户最关心的关键词
- 避免将不同项目的信息混合在一起描述

[严格禁止的行为]
- 不经过搜索验证直接回答用户问题
- 绝对不允许使用markdown语法
- 绝对不允许出现任何**或*等加粗格式
- 绝对不允许说"并非一个通用技术术语或广为人知的...非广为人知的信息或通用技术术语" "根据搜索结果显示..." "目前未发现相关信息..."等无意义表述

[最终回复的格式要求]
第一行: [KEY] :: <关键词> | <关键词>  <...>
第二行: >> [agent enable] 
第三行开始: <详细解释>
最后一行: [LLM] :: {model_names}

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
            
            # 持续执行直到没有工具调用或达到最大轮次
            max_iterations = 15
            iteration = 0
            result = None
            
            while iteration < max_iterations:
                iteration += 1                
                result = await self._planning_agent.ainvoke(messages)                
                # 将AI响应添加到消息历史
                messages.append(result)
                logger.info(f"规划专家第 {iteration} 轮响应: {result}")
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
                        if tool_name == 'smart_search':
                            tool_result = await smart_search.ainvoke(tool_call['args'])
                        elif tool_name == 'jina_fetch_webpage':
                            tool_result = await jina_fetch_webpage.ainvoke(tool_call['args'])
                        # elif tool_name == 'nbnhhsh':
                            # tool_result = await nbnhhsh.ainvoke(tool_call['args'])
                        else:
                            tool_result = f"未知工具: {tool_name}"
                        
                        # 记录统计信息
                        tool_end_time = time.time()
                        tool_duration = tool_end_time - tool_start_time
                        tool_stats[tool_name]['count'] += 1
                        tool_stats[tool_name]['total_time'] += tool_duration
                        
                        # 将工具结果添加到消息历史
                        messages.append(ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call['id']
                        ))
                        
                        logger.info(f"工具 {tool_name} 执行完成，耗时 {tool_duration:.2f}s")
                
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
            elapsed_line = f"[elapsed] :: {' | '.join(elapsed_parts)}"
            
            if tool_stats:
                stats_parts = []
                for tool_name, stats in tool_stats.items():
                    stats_parts.append(f"[{tool_name}: {stats['count']}]")
                tool_stats_line = f"[use tools] :: {' | '.join(stats_parts)}"
            
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
                fallback_content = f"[KEY] :: 信息处理 | 处理异常\n>> [agent enable]\n抱歉，暂时无法生成完整的回复内容。{stats_text}\n[LLM] :: {model_names}"
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
            elapsed_line = f"[elapsed] :: {' | '.join(elapsed_parts)}"
            
            if tool_stats:
                stats_parts = []
                for tool_name, stats in tool_stats.items():
                    stats_parts.append(f"[{tool_name}: {stats['count']}]")
                tool_stats_line = f"[use tools] :: {' | '.join(stats_parts)}"
            
            stats_parts = []
            if tool_stats_line:
                stats_parts.append(tool_stats_line)
            stats_parts.append(elapsed_line)
            
            stats_text = "\n" + "\n".join(stats_parts)
            
            # 检查是否是内容审查失败
            error_msg = str(e)
            if "data_inspection_failed" in error_msg:
                fallback_content = f"[KEY] :: 内容审查 | 审查失败\n>> [agent enable]\n输入内容可能包含不当信息，无法处理。错误详情: {error_msg}{stats_text}\n[LLM] :: {model_names}"
            elif "inappropriate content" in error_msg.lower():
                fallback_content = f"[KEY] :: 内容过滤 | 内容限制\n>> [agent enable]\n内容被服务商过滤，无法生成回答。错误: {error_msg}{stats_text}\n[LLM] :: {model_names}"
            else:
                fallback_content = f"[KEY] :: 系统异常 | 执行错误\n>> [agent enable]\n系统处理异常: {error_msg}{stats_text}\n[LLM] :: {model_names}"
            
            return AIMessage(content=fallback_content)
    

