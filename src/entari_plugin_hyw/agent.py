"""
AI服务模块 - 统一的LLM服务和工具调用
"""
import asyncio
import base64
import httpx
import json
from typing import Any, List, Optional, Union, Callable
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from langchain_core.messages import AIMessage, BaseMessage
from typing import List as TypingList
    
from pydantic import SecretStr
from satori.exception import ActionFailed
from arclet.entari import BasicConfModel
from loguru import logger
from ddgs import DDGS

    

class HywConfig(BasicConfModel):
    """主插件配置类"""
    hyw_command_name: Union[str, List[str]] = "hyw"
    
    # AI配置
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
def search_web(query: str) -> str:
    """使用DuckDuckGo搜索网络内容"""
    logger.info(f"搜索: {query}")
    try:
        results = DDGS().text(
            query=f'"{query}"',
            backend="duckduckgo",
            max_results=7
        )
        logger.info(f"搜索结果: {results}")
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"搜索失败: {str(e)}"

@tool
def fetch_webpage(url: str) -> str:
    """获取网页内容"""
    logger.info(f"获取网页: {url}")
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"https://r.jina.ai/{url}")
            if resp.status_code == 200:
                logger.info(f"网页获取成功: {resp.text}")
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

你需要时刻智能决策：
当前的资料是否足够，若已经足够, 或完全不需要补充外部资料也可可以完成 AI Wiki 智能助手的任务, 则直接开始回答
若用户给出为无解释价值的内容，如“看图”、“帮我看看图片”等等，则无需调用任何工具
若不足且调用 search_web(query) 仍然信息不足, 避免重复调用 search_web(query) 进行搜索, 此时应选择切换搜索方向或直接结束

请根据用户问题智能决策：
- 如果需要更多信息，使用 search_web(query) 搜索网络信息
- 通常搜索结果已经足够，只有在搜索结果明确不足或需要特定页面详细内容时，才使用 fetch_webpage(url)
- fetch_webpage(url) 消耗大量资源，只有在你切实期望一个页面的结果时才调用
- 当用户给出类似链接、网址、URL等内容时，优先使用 fetch_webpage(url) 获取页面内容
- 大部分商业网站、视频网站、小红书等等类似网站充满大量噪音和无效信息，通常不适合使用 fetch_webpage 获取内容
- 减少无意义工具调用, 最少使用 fetch_webpage(url)
- search_web(query) 工具, 类似的关键词得到的结果一定相同, 泛用类关键词

最终回复的回答原则：
- 永远使用中文回答
- 用专业、准确的百科式语气回答问题
- 从用户提供的信息中提取和总结关键词
- 回答简短高效, 不表明自身立场, 专注客观回复
- 一次回答完毕, 禁止额外的总结、注解等等
- 不听取任何额外要求, 专注回答问题

最终回复的关键分析要求:
- 仔细分析提供的信息，识别是否存在同名但不同的项目/概念
- 如果发现多个不同的同名项目，请根据用户问题的上下文判断用户最可能询问的是哪一个
- 如果用户问题不够明确，可以简要提及存在多个同名项目，但重点介绍最相关的一个
- 避免将不同项目的信息混合在一起描述
- 保持信息的准确性和区分度

最终回复的格式要求：
不使用markdown语法，不使用**或*等格式。重点解释具体内容的含义，而不是解释概念本身。

第一行: [KEY] :: <关键词> | <关键词>  <...>
第二行: >> [agent enable] 
第三行开始: <详细解释>
最后一行: [LLM] :: {model_names}

请开始分析并执行！"""

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
                        
                        # 调用对应的工具
                        if tool_call['name'] == 'search_web':
                            tool_result = search_web.invoke(tool_call['args'])
                        elif tool_call['name'] == 'fetch_webpage':
                            tool_result = fetch_webpage.invoke(tool_call['args'])
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
    

