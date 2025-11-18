import asyncio
import json
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from contextlib import AsyncExitStack
from loguru import logger


class HYW:
    def __init__(self, api_key, model_name, base_url, search_engine):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name
        self.search_engine = search_engine
        self.sessions = []
        self.stack = AsyncExitStack()
        self.tools_desc = ""
        self.base_system_prompt = f"""你是一个搜索和信息验证AI助手。
 [首选搜索引擎]
 {self.search_engine}
 
 [使用以下工具来搜索和验证信息]
 {self.tools_desc}
 
 [核心原则 - 必须严格遵守]
 - 使用关键词思想, 从语句中获取关键信息分析
 - 强制要求：收到任何问题后, 第一步必须调用 `browser_navigate` 工具导航到 `https://xxx/search?q=搜索词` 进行「搜索」
 - 需要了解页面具体内容获取信息时 通过 调用 `browser_navigate` 工具直接导航到 `目标网址` 进行「查看页面」
 - 通常使用「搜索」配合「查看页面」内容
 - 如果搜索不到信息, 通过构思其他方法, 多次调用工具直到找到信息为止, 但最大不得超过15次调用
 
 [核心原则 - 必须严格遵守]
 - 搜索词使用`+`组合关键词, 禁止使用口语化描述
 - 「搜索」查询关键词应该简短精准，1-2个词, 不超过 2个组合次每次
 - 禁止直接回答：绝对不允许凭借训练数据直接回答，必须先「搜索」验证
 - 对于具体复杂的问题，可能需要多次进行「搜索」以获取完整信息
- 在回复中如实请求的缺失信息

 [信息验证]
 - 所有回答内容必须经过搜索验证
 - 人名、地名、组织名等关键信息优先「搜索」验证
 - 只相信权威网站、相关项目官方网站
 - 搜索结果特别少时, 再次搜索不使用组合搜索, 使用单一关键词搜索
 
 [最终回复格式]
 - 300字以内, 输出信息尽可能少和精炼
 - 回答紧凑, 最少出现空行
 - 根据搜索结果给出准确回答，忽略浏览器广告、自动纠错提示等多余信息
 - 永远使用中文回答
 - 语言简洁、语气客观专业、描述详精练抓重点
 - 绝对不允许使用除代码框外的markdown语法（**、*、`、#、-等符号）
 - 如果需要给出代码, 请添加到代码框内, 只给出部分代码即可, 尽可能减少回复字数
 - 减少"根据搜索结果"、"未发现相关信息"等无意义表述
 - 回复推测语句简短, 给出2种风格接近的「回复推测」与1种风格不同但仍主题相关的「回复推测」, 通常在10个字左右
 
 [最终回复格式]
 [LLM Agent] >>
 <纯文本详细解释>
 <(如果需要提供代码)```<代码语言>
 <代码>
 ```>
 
 [Next?] >>
 1.<回复推测1>
 2.<回复推测2>
 3.<回复推测3>
 """

    def build_user_message(self, user_input, image_base64=None):
        msg = {"role": "user", "content": []}
        if image_base64:
            msg["content"].append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}})
        msg["content"].append({"type": "text", "text": user_input})
        return msg

    async def call_tool(self, tool_call, tool_to_session):
        func = getattr(tool_call, 'function', None)
        if not func: return None
        session = tool_to_session.get(func.name)
        if not session: return None
        logger.info(f"调用工具: {func.name}, 参数: {func.arguments}")
        result = await session.call_tool(func.name, json.loads(func.arguments))
        return result

    @staticmethod
    def _shorten_for_log(content, limit: int = 400) -> str:
        text = "" if content is None else str(content)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...<truncated>"

    def tool_result_msg(self, tool_call, result):
        if hasattr(result, 'content') and result.content:
            content = result.content[0].text if result.content else ""
        else:
            content = str(result)
        logger.info(f"工具结果内容: {self._shorten_for_log(content)}")
        return {"role": "tool", "tool_call_id": tool_call.id, "content": content}

    def tool_error_msg(self, tool_call, error):
        return {"role": "tool", "tool_call_id": tool_call.id, "content": f"错误: {error}"}

    async def connect_servers(self, servers_config):
        await self.stack.__aenter__()
        for s in servers_config:
            t = await self.stack.enter_async_context(stdio_client(StdioServerParameters(command=s["command"], args=s["args"])))
            session = await self.stack.enter_async_context(ClientSession(t[0], t[1]))
            await session.initialize()
            self.sessions.append({"name": s["name"], "session": session})
            logger.info(f"Connected to server: {s['name']} t: {t}")
        return self.stack, self.sessions
 
    async def agent(self, user_input, image_base64=None, conversation_history=None):
        if conversation_history is None:
            conversation_history = []
            
        async def collect_tools(sessions):
            all_tools, tool_to_session = [], {}
            for item in sessions:
                session = item["session"]
                tools = await session.list_tools()
                for t in tools.tools:
                    if t.name not in ["browser_navigate"]:
                        continue
                    all_tools.append({
                        "type": "function",
                        "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema}
                    })
                    tool_to_session[t.name] = session
            return all_tools, tool_to_session
        
        all_tools, tool_to_session = await collect_tools(self.sessions)
        user_msg = self.build_user_message(user_input, image_base64)
        
        messages = []
        if not conversation_history or conversation_history[0]["role"] != "system":
            messages.append({"role": "system", "content": self.base_system_prompt.format(tools_desc="\n".join([f"- {t['function']['name']}" for t in all_tools]))})
        messages.extend(conversation_history)
        messages.append(user_msg)
        
        logger.info(f"开始处理用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
        logger.info(f"历史对话长度: {len(conversation_history)}")
        llm_final_response = ""
        for i in range(25):
            resp = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                tools=all_tools,
                tool_choice="auto"
            )
            msg = resp.choices[0].message
            logger.info(f"LLM响应: content={bool(msg.content)}, tool_calls={bool(msg.tool_calls)}")
            
            assistant_message_content = msg.content if msg.content is not None else ""
            messages.append({"role": "assistant", "content": assistant_message_content})
            
            # 如果有工具调用，执行工具
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    func_name = getattr(getattr(tc, "function", None), "name", "unknown")
                    try:
                        result = await self.call_tool(tc, tool_to_session)
                        logger.info(
                            "工具 %s 返回: %s",
                            func_name,
                            self._shorten_for_log(result)
                        )
                        messages.append(self.tool_result_msg(tc, str(result)))
                    except Exception as e:
                        logger.error(f"工具调用失败: {e}")
                        logger.info("工具 %s 失败详情: %s", func_name, self._shorten_for_log(e))
                        messages.append(self.tool_error_msg(tc, e))
            # 如果有文本内容且没有工具调用，说明对话结束
            elif msg.content:
                logger.success(f"end of conversation, final LLM response generated")
                llm_final_response = msg.content
                break        
        logger.info(f"最终响应: {llm_final_response[:200]}{'...' if len(llm_final_response) > 200 else ''}")
        return {"llm_response": llm_final_response, "conversation_history": [msg for msg in messages if msg["role"] != "system"]}
