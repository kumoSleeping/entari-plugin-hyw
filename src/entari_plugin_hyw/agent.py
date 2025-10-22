import asyncio
import base64
import httpx
from typing import Any, List, Optional, Union, Callable
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from arclet.entari import BasicConfModel, metadata, plugin_config
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
    
from pydantic import SecretStr
from satori.exception import ActionFailed
from loguru import logger

# 导入工具模块
from .tools import (
    web_search, 
    exact_search, 
    jina_fetch_webpage, 
    nbnhhsh,
    set_global_search_engine
)

# 导入视觉分析模块
from .vision import vision_expert_analysis

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

SYS_PROMPT = """你是一个大语言模型驱动的智能解释器, 需要使用工具获取准确信息, 回答用户的问题.

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

[理解信息]
- 特别需要注意一切大语言模型容易产生幻觉的内容, 包括但不限于:
    - 人名、地名、组织机构名等专有名词
    - 某某某常被误拼写为...
    - 事件的时间、地点、参与人员等细节
> 遇到上述内容请务必使用工具进行验证和确认

[工具调用限制] [重要]
- 用户需要快速获取准确的信息, 你必须合理规划使用工具的顺序和频率, 避免滥用工具

[工具说明]
- web_search 
    - 此工具传入列表进行多个独立搜索任务, 请勿重复搜索相同关键词相关内容
    - 先使用 web_search 工具获取准确知识后再进行回复，严禁凭借训练数据直接回答
    - 过滤偏离主题的噪音信息, 构建不同组合、精细度、具体性的关键词列表进行二次搜索
    - 信息从搜索引擎获取, 请自动判断是否内容符合主题, 筛选符合主题的内容
- exact_search
    - 对于完全未知的复合词、新兴事物、冷门名词等, 优先使用 exact_search 工具进行搜索
    - 传入内容必须为一个完整的查询字符串
    - 禁止查询短语、语句、询问
    - 只能用于搜索单个名词或专有名词
    - 不能搜索组合词汇
    - 先使用 exact_search 工具获取准确知识后再进行回复，严禁凭借训练数据直接回答
    - 信息从搜索引擎获取, 请自动判断是否内容符合主题, 筛选符合主题的内容
- jina_fetch_webpage
    - 如果用户给出类似链接 网址 URL 或潜在能找到网址的内容时，一定要使用工具查找和获取相关网页, 使用 jina_fetch_webpage 获取网页内容, 仔细分析网页内容以补充回答
- nbnhhsh
    - 此工具只能用于还原一个中文拼音、中文互联网迷因、im口头禅缩写的所有可能性
    - 此工具具有大量噪音, 获取后请根据上下文判断筛选合适的意义
    - 信息从公共数据库获取

[信息获取偏好]
- 优先使用官方可靠的信息源：
    - 维基百科、萌娘百科等百科网站（信息相对准确）
    - 官方网站、官方Wiki（最权威）
    - 多个独立来源的一致信息（交叉验证）

[智能搜索策略]
在开始搜索前，请先规划信息获取的优先级顺序：
1. 首先获取基础定义和核心概念（这是什么？）
2. 然后获取详细信息和具体内容（详细信息？）
3. 接着验证信息的准确性和来源（是否准确？）
4. 最后补充背景信息和相关内容（背景是什么？）

[exact_search优先使用原则]
- 对于任何不理解的、可能为用户名、ID、特定术语的完整字符串，必须优先使用 exact_search
- 特别是：不包含空格的连续字母数字组合
- exact_search 是查找用户特定信息的最有效工具，应该作为第一选择
- 只有当 exact_search 无结果时，才转向其他搜索策略

[领域特定搜索策略]
- 游戏/动漫角色查询：优先搜索"游戏名 + 角色名"
- 音乐/体育相关：优先搜索"队名 + 成员"
- 遇到网站名：优先搜索"网站名 + 关键词"的组合

禁止使用噪音信息源：
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

[信息验证强制要求]
- 对于人名、角色名、组织名等专有名词, 必须通过至少两个不同来源进行交叉验证
- 如果搜索结果之间存在矛盾, 必须明确指出并提供所有可能的信息
- 绝对不能将不同项目、不同角色的信息混合在一起描述
- 在不确定的情况下, 必须明确说明"信息可能存在争议"或"需要进一步验证"
- 遇到可能的人名, 可以搜索一些社交媒体如 微博、Twitter B站 Github 等平台进行验证是否存在该人物

- [格式要求]
    第一行: [Key] :: <关键词> | <关键词>  <...>
    第二行: >> [LLM Agent] :: {model_names}
    第三行开始: <详细解释>

"""

class AgentService:
    """AI服务类，管理文本和视觉LLM"""
    
    def __init__(self, config: "HywConfig"):
        self.config = config
        self._text_llm: Optional[ChatOpenAI] = None
        self._vision_llm: Optional[ChatOpenAI] = None
        self._planning_agent: Optional[Any] = None
        
        # 删除信息规划相关变量
        
        # 设置全局搜索引擎配置
        self._set_search_engine()
        self._init_models()
        self._init_agents()
        self._setup_compressor()
    
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
        
    
    def _init_agents(self):
        """初始化专家Agent系统 - 使用 create_agent"""
        if self._text_llm is None:
            self._planning_agent = None
            return
        
        # 创建用于代理的LLM实例
        agent_llm = ChatOpenAI(
            model=self.config.text_llm_model_name,
            api_key=SecretStr(self.config.text_llm_api_key),
            base_url=self.config.text_llm_model_base_url,
            temperature=0.2,  # 较低温度，保持规划的一致性
            extra_body={"enable_search": False}
        )
        
        
        # 定义所有工具
        all_tools = [web_search, exact_search, jina_fetch_webpage, nbnhhsh]
        
        # 构建模型名称字符串
        model_names = self.config.text_llm_model_name
        
        system_prompt = SYS_PROMPT.format(model_names=model_names)
                
        # 记录提示词信息用于调试
        logger.info(f"[DEBUG] Tools count: {len(all_tools)}")
        logger.info(f"[DEBUG] Tools names: {[tool.name for tool in all_tools]}")
        
        # 使用 create_agent 创建生产就绪的代理实现
        self._planning_agent = create_agent(
            model=agent_llm,
            tools=all_tools,
            system_prompt=system_prompt,
            # middleware=[handle_tool_errors]  # 添加工具错误处理中间件
        )
        
        logger.info(f"[DEBUG] Agent created successfully: {type(self._planning_agent)}")

    def _setup_compressor(self):
        """设置压缩器LLM"""
        # 创建压缩器LLM - 使用较低温度保证压缩质量
        compressor_llm = ChatOpenAI(
            model=self.config.text_llm_model_name,
            api_key=SecretStr(self.config.text_llm_api_key),
            base_url=self.config.text_llm_model_base_url,
            temperature=0.1,  # 低温度，保证压缩的一致性和准确性
            extra_body={"enable_search": False}
        )
        
        # 设置全局压缩器LLM
        from .tools import set_global_compressor_llm
        set_global_compressor_llm(compressor_llm)
        logger.info(f"[DEBUG] 压缩器LLM设置完成: {self.config.text_llm_model_name}")

    
    async def unified_completion(self, content: str, images: Optional[List[bytes]] = None, react_func: Optional[Callable[[str], Any]] = None) -> Any:
        """统一入口 - 使用 LangChain 自动工具执行（带内容过滤重试）"""
        if self._planning_agent is None:
            raise RuntimeError("规划专家未初始化")
        
        # 内容过滤重试机制
        max_inspection_retries = 3
        retry_count = 0
        
        while retry_count <= max_inspection_retries:
            try:
                result = await self._unified_completion_internal(content, images, react_func)
                
                # 检查返回结果中是否包含内容审查失败的信息
                if hasattr(result, 'content') and "内容审查 | 审查失败" in result.content and retry_count < max_inspection_retries:
                    retry_count += 1
                    logger.warning(f"[内容审查失败] 第 {retry_count}/{max_inspection_retries} 次重试")
                    # 继续下一次尝试
                    continue
                
                return result
                
            except Exception as e:
                # 检查是否是内容审查相关的异常
                error_msg = str(e)
                if ("data_inspection_failed" in error_msg or "inappropriate content" in error_msg.lower()) and retry_count < max_inspection_retries:
                    retry_count += 1
                    logger.warning(f"[内容审查失败] 异常错误，第 {retry_count}/{max_inspection_retries} 次重试: {error_msg}")
                    # 继续下一次尝试
                    continue
                else:
                    # 其他异常或超过重试次数，直接返回
                    raise
    
    async def _unified_completion_internal(self, content: str, images: Optional[List[bytes]] = None, react_func: Optional[Callable[[str], Any]] = None) -> Any:
        """统一入口内部实现 - 使用 create_agent 自动工具执行"""
        logger.info(f"[DEBUG] 开始处理内容: {content[:100]}...")
        if self._planning_agent is None:
            logger.error("[DEBUG] 规划专家未初始化")
            raise RuntimeError("规划专家未初始化")
        
        # 开始计时 - 包含整个处理流程
        import time
        total_start_time = time.time()
        
        # 收集专家信息和使用的模型
        expert_info = []
        model_names = self.config.text_llm_model_name
        logger.info(f"[DEBUG] 使用模型: {model_names}")
        
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
                    vision_result = await vision_expert_analysis(self._vision_llm, image_data, content)
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
        
        try:
            # 3. 使用传统create_agent方式处理
            logger.info("=== 使用 create_agent 开始处理 ===")
            
            # 构建用户消息 - 强调必须使用工具
            user_message = f"""[当前情况]
{full_context}

[开始]
- 合理规划使用工具的顺序和频率, 避免滥用工具与资源
- 请根据以上信息进行分析和回复
"""
                        
            # 调用代理，create_agent 会自动处理工具调用循环
            result = await self._planning_agent.ainvoke({
                "messages": [HumanMessage(content=user_message)]
            })
            
            
            # 详细检查结果结构
            if isinstance(result, dict):
                if 'messages' in result:
                    logger.info(f"[DEBUG] 字典中的消息数量: {len(result['messages'])}")
                    for i, msg in enumerate(result['messages']):
                        msg_type = type(msg).__name__
                        content_len = len(str(msg.content)) if hasattr(msg, 'content') else 0
                        tool_calls_count = len(msg.tool_calls) if hasattr(msg, 'tool_calls') and msg.tool_calls else 0
            elif hasattr(result, 'messages'):
                logger.info(f"[DEBUG] 对象消息数量: {len(result.messages)}")
                for i, msg in enumerate(result.messages):
                    msg_type = type(msg).__name__
                    content_len = len(str(msg.content)) if hasattr(msg, 'content') else 0
                    tool_calls_count = len(msg.tool_calls) if hasattr(msg, 'tool_calls') and msg.tool_calls else 0
                    logger.info(f"[DEBUG] 对象消息{i}: {msg_type}, 内容长度: {content_len}, 工具调用数: {tool_calls_count}")
            else:
                logger.warning(f"[DEBUG] 未知结果结构，长度: {len(str(result))} 字符")
            
            # 计算总耗时
            total_duration = time.time() - total_start_time
            
            # 生成统计信息
            elapsed_parts = [f"total: {total_duration:.1f}s"]
            if vision_time > 0:
                elapsed_parts.append(f"vision: {vision_time:.1f}s")
            elapsed_line = f"[Elapsed] :: {' | '.join(elapsed_parts)}"
            
            # 提取工具使用统计 - 使用列表推导简化
            messages = result.get('messages', []) if isinstance(result, dict) else (result.messages if hasattr(result, 'messages') else [])
            tool_calls = [call for msg in messages if hasattr(msg, 'tool_calls') and msg.tool_calls for call in msg.tool_calls]
            tool_stats = {name: {'count': len([call for call in tool_calls if call.get('name') == name])} 
                         for name in set(call.get('name', 'unknown') for call in tool_calls)}
            
            if tool_stats:
                tool_parts = [f"{f'web_search[{self.config.search_engine}]' if name == 'web_search' else name}: {stats['count']}" for name, stats in tool_stats.items()]
                tool_stats_line = f"[Use Tools] :: {', '.join(tool_parts)}"
            else:
                tool_stats_line = ""
            
            # 简化结果处理 - 直接获取最后一条消息
            messages = result.get('messages', []) if isinstance(result, dict) else (result.messages if hasattr(result, 'messages') else [])
            
            # 使用列表推导找到最后一条AI消息（有内容且无工具调用）
            ai_messages = [msg for msg in messages if hasattr(msg, 'content') and msg.content and not (hasattr(msg, 'tool_calls') and msg.tool_calls)]
            
            if ai_messages:
                ai_content = ai_messages[-1].content
                stats_text = f"\n{tool_stats_line}\n{elapsed_line}" if tool_stats_line else f"\n{elapsed_line}"
                return AIMessage(content=ai_content + stats_text)
            else:
                fallback_content = f"[Key] :: 信息处理 | 处理完成\n>> [LLM Agent] :: {model_names}\n处理完成，但未生成有效回复。\n{elapsed_line}"
                return AIMessage(content=fallback_content)
                
        except Exception as e:
            # 获取统计信息和耗时
            total_duration = time.time() - total_start_time
            # 内联统计信息生成
            elapsed_parts = [f"total: {total_duration:.1f}s"]
            if vision_time > 0:
                elapsed_parts.append(f"vision: {vision_time:.1f}s")
            stats_text = f"\n[Elapsed] :: {' | '.join(elapsed_parts)}"
            
            # 内容过滤错误处理
            error_msg = str(e)
            is_inspection_error = "data_inspection_failed" in error_msg or "inappropriate content" in error_msg.lower()
            
            if is_inspection_error:
                logger.warning(f"内容审查失败: {error_msg}")
                fallback_content = f"[Key] :: 内容审查 | 审查失败\n>> [LLM Agent] :: {model_names}\n内容审查失败，请重新尝试。{stats_text}"
                return AIMessage(content=fallback_content)
            
            # 通用错误处理
            logger.error(f"create_agent 执行失败: {e}")
            fallback_content = f"[Key] :: 系统异常 | 执行错误\n>> [LLM Agent] :: {model_names}\n系统异常: {error_msg}\n{stats_text}"
            return AIMessage(content=fallback_content)
