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
    




# 搜索工具
@tool
async def search_web(queries: List[str]) -> str:
    """搜索网络内容，支持单个查询或最多3个查询的列表，每个查询都进行严格搜索"""
    
    
    logger.info(f"搜索查询: {queries}")
    
    try:
        # 直接调用duck_search_real，传入关键词列表，函数内部会并发处理
        results = await duck_search_real(
            keywords=queries, 
            max_results=5,
            region="zh-cn"
        )
        
        # 按关键词分组结果
        grouped_results = {}
        for result in results:
            keyword = result.get('keyword', 'unknown')
            if keyword not in grouped_results:
                grouped_results[keyword] = []
            grouped_results[keyword].append(result)
        
        # 构建最终结果格式
        all_results = [
            {
                "query": query,
                "results": grouped_results.get(query, []),
                "error": None
            }
            for query in queries
        ]
        
        logger.info(f"搜索结果: {all_results}")
        return json.dumps(all_results, ensure_ascii=False, indent=2)
        
    except Exception as e:
        # 错误处理
        error_results = [
            {
                "query": query,
                "results": [],
                "error": str(e)
            }
            for query in queries
        ]
        logger.error(f"搜索失败: {e}")
        return json.dumps(error_results, ensure_ascii=False, indent=2)


@tool
async def fetch_webpage(url: str) -> str:
    """获取网页内容"""
    logger.info(f"获取网页: {url}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"https://r.jina.ai/{url}")
            if resp.status_code == 200:
                logger.info(f"网页获取成功，长度: {len(resp.text)}")
                return resp.text
            else:
                return f"获取网页失败，状态码: {resp.status_code}"
    except Exception as e:
        return f"获取网页失败: {str(e)}"

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
        
        vision_prompt = """你是视觉分析专家，请简洁分析图片内容：

- 一段话详尽的描述主要内容, 
- 如果出现文字, 请给出所有文字内容
- 如果用户有提出具体问题，同样作为描述主要内容和关键词的一部分
- 总结3-5个核心关键词，方便后续搜索使用
- 关键词一定不要出现迷惑性、宽泛性信息, 如 迷因、漫画、风景、抽象等等容易迷惑搜索工具的词语
- 关键词贴近图片内容, 方便后续搜索使用的关键词

输出格式：
第一行: 描述：<简短描述>
第二行: 关键词：<关键词> <关键词> <...>

保持简洁，为后续分析提供核心信息即可。"""
        
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
        
        # 为规划专家绑定所有工具，让它完全控制整个流程
        all_tools = [search_web, fetch_webpage]
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
        
        # 收集专家信息和使用的模型
        expert_info = []
        model_names = self.config.text_llm_model_name
        
        # 1. 如果有图片，先调用视觉专家进行分析
        if images:
            if react_func:
                await react_func("127847")  # 🍧 图片分析中
            model_names += f"[{self.config.vision_llm_model_name}]"
            for i, image_data in enumerate(images):
                if self._vision_llm is None:
                    vision_result = "视觉专家不可用"
                else:
                    vision_result = await _vision_expert_analysis(self._vision_llm, image_data, content)
                expert_info.append(f"视觉专家分析{i+1}: {vision_result}")
        
        if react_func:
            await react_func("10024")  # ✨ 开始智能规划
        
        # 2. 构建完整上下文
        context_parts = [f"用户问题: {content}"]
        if expert_info:
            context_parts.extend(expert_info)
        
        full_context = "\n".join(context_parts)
        
        # 3. 使用 LangChain 自动工具执行机制
        planning_prompt = f"""你是AI Wiki智能助手，目标是回答用户疑问的"这是什么"。

当前情况：
{full_context}

你需要智能识别以下场景, 做出不同的反应:
- 这是一句用户之间的对话, 我需要你去要从中过滤掉无关人员之间的对话信息、如人名与可能的上下文产物, 解释某一个用户对这句话中不理解的关键词
- 用户在向我提问这句话
- 这是一张视觉专家分析后的多媒体内容, 我需要理解其中的意义并进行解释这张图片
- 这是一张视觉专家分析后的多媒体内容, 我需要在其中排除掉干扰信息, 抓取关键信息进行百科

关于智能决策使用工具：
- 当前的资料是否足够，若已经足够, 或完全不需要补充外部资料也可可以完成 AI Wiki 智能助手的任务, 则直接开始回答
- 若用户给出为无搜索价值的内容，如"1+1=几"、"帮我看看图片", "地球是圆的还是方的"等等，则无需调用任何工具
search_web():
    - 如果遇到非广为人知的词语或通用技术术语, 请一定使用搜索网络信息获取知识补充
    - 搜索工具传入列表可以同时分开关键词进行多次搜索, 例如 ["python github", "python 如何打包"]
fetch_webpage():
    - 通常搜索结果已经足够，只有在搜索结果明确不足或需要特定页面详细内容时，才使用 fetch_webpage(url)
    - 当用户给出类似链接、网址、URL等内容时，优先使用 fetch_webpage(url) 获取页面内容
    - 大部分商业网站、视频网站、小红书等等类似网站充满大量噪音和无效信息，通常不适合使用 fetch_webpage 获取内容

最终回复的回答原则：
- 永远使用中文回答
- 用专业、准确的百科式语气回答问题
- 不需要每个关键词都解释, 只解释用户最关心的关键词
- 避免将不同项目的信息混合在一起描述
- 永远不要出现"非一个广为人知的信息或通用技术术语"等类似表述, 多利用工具获取信息
- 回答简短高效, 不表明自身立场, 专注客观回复

最终回复的格式要求：
- 不使用markdown语法
- 不使用**或*等格式

第一行: [KEY] :: <关键词> | <关键词>  <...>
第二行: >> [agent enable] 
第三行开始: <详细解释>
最后一行: [LLM] :: {model_names}

开始分析并执行！"""

        logger.info(f"当前 planning_prompt: {planning_prompt} ")

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
                logger.info(f"规划专家第 {iteration} 轮结果: {result}")
                # 检查是否有工具调用
                if hasattr(result, 'tool_calls') and result.tool_calls:
                    logger.info(f"执行工具调用: {[tc['name'] for tc in result.tool_calls]}")
                    
                    # 执行每个工具调用
                    for tool_call in result.tool_calls:
                        
                        # 调用对应的工具（现在都是异步的）
                        if tool_call['name'] == 'search_web':
                            tool_result = await search_web.ainvoke(tool_call['args'])
                        elif tool_call['name'] == 'fetch_webpage':
                            tool_result = await fetch_webpage.ainvoke(tool_call['args'])
                        else:
                            tool_result = f"未知工具: {tool_call['name']}"
                        
                        # 将工具结果添加到消息历史
                        messages.append(ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call['id']
                        ))
                        
                        logger.info(f"工具 {tool_call['name']} 执行完成")
                
                # 如果没有工具调用，说明模型自己决定停止并直接返回回答
                if not (hasattr(result, 'tool_calls') and result.tool_calls):
                    logger.info("没有更多工具调用，模型直接返回回答")
                    break
            
            # 检查最终结果
            if result and hasattr(result, 'content') and result.content:
                return result
            else:
                fallback_content = f"[KEY] :: 信息处理 | 处理异常\n>> [agent enable]\n抱歉，暂时无法生成完整的回复内容。\n[LLM] :: {model_names}"
                return AIMessage(content=fallback_content)
                
        except Exception as e:
            logger.error(f"规划专家执行失败: {e}")
            
            # 检查是否是内容审查失败
            error_msg = str(e)
            if "data_inspection_failed" in error_msg:
                fallback_content = f"[KEY] :: 内容审查 | 审查失败\n>> [agent enable]\n输入内容可能包含不当信息，无法处理。错误详情: {error_msg}\n[LLM] :: {model_names}"
            elif "inappropriate content" in error_msg.lower():
                fallback_content = f"[KEY] :: 内容过滤 | 内容限制\n>> [agent enable]\n内容被服务商过滤，无法生成回答。错误: {error_msg}\n[LLM] :: {model_names}"
            else:
                fallback_content = f"[KEY] :: 系统异常 | 执行错误\n>> [agent enable]\n系统处理异常: {error_msg}\n[LLM] :: {model_names}"
            
            return AIMessage(content=fallback_content)
    

