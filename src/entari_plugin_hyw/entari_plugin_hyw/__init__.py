"""
entari-plugin-hyw - HywCore Integration
"""
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from datetime import datetime
import re
import json

from arclet.alconna import Alconna, Args, AllParam, Arparma
from arclet.entari import metadata, listen, Session, plugin_config, BasicConfModel, command
from arclet.entari import MessageChain, Text, Image, MessageCreatedEvent, Quote, At
from arclet.entari.event.lifespan import Cleanup, Startup
from arclet.entari.event.command import CommandReceive
from loguru import logger

# 引入全新的 Core
from hyw_core import HywAgent, FlowRunner, ToolRegistry, HywCoreConfig, AgentSession, load_tools, warmup, chat_flow
from .history import HistoryManager
from .misc import process_images
from hyw_core.tools._public.browser.manager import close_shared_browser
# 渲染功能 (不再作为 LLM 工具，由插件层根据结构化输出调用)
from hyw_core.tools.render import render as render_card

def format_conversation_log(api_messages: list) -> str:
    """
    格式化对话记录，直接保存原始内容
    - user: 用户消息
    - sys: 系统消息（持久）
    - sys_temp: 临时系统消息
    - llm: LLM 回复（assistant）
    - tool: 工具结果
    """
    blocks = []
    for msg in api_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # 处理多模态消息（content 是列表时），用占位符替代图片
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        parts.append("[IMAGE]")
                else:
                    parts.append(str(part))
            content = " ".join(parts)

        # 确定标签类型
        if role == "user":
            tag = "user"
        elif role == "system":
            tag = "sys_temp" if msg.get("_temp") else "sys"
        elif role == "assistant":
            tag = "llm"
        else:
            tag = role

        # 直接保存原始内容，不做解析
        blocks.append(f"```{tag}\n{content}\n```")

    return "\n\n".join(blocks)


def extract_references_from_messages(api_messages: list) -> list:
    """从 api_messages 中提取搜索结果作为 references"""
    references = []
    index = 1

    for msg in api_messages:
        content = msg.get("content", "")
        # 处理 content 是列表的情况（多模态消息）
        if isinstance(content, list):
            # 从列表中提取文本内容
            text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            content = " ".join(text_parts)
        # 检查是否是工具结果消息
        if msg.get("role") == "user" and isinstance(content, str) and content.startswith("[Tool Result:"):
            try:
                # 提取 JSON 部分
                json_start = content.find("{")
                if json_start == -1:
                    continue
                json_str = content[json_start:]
                parsed = json.loads(json_str)

                # 提取搜索结果
                results = parsed.get("results", [])
                for res in results:
                    references.append({
                        "title": res.get("title", f"Result {index}"),
                        "url": res.get("url", ""),
                        "snippet": res.get("snippet", "") or res.get("content", ""),
                        "original_idx": index,
                    })
                    index += 1
            except (json.JSONDecodeError, TypeError):
                continue

    return references

def save_conversation_log(api_messages: list, user_input: str) -> Optional[Path]:
    """
    将对话记录保存为本地 markdown 文件，直接保存原始内容
    """
    if not api_messages:
        return None

    # 创建日志目录
    log_dir = Path("logs/conversations")
    log_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名：时间戳 + 用户输入摘要
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = re.sub(r'[^\w\u4e00-\u9fff]', '_', user_input[:20]).strip('_')
    filename = f"{timestamp}_{summary}.md"
    filepath = log_dir / filename

    # 生成 markdown 内容
    content_parts = [
        f"# 对话记录",
        f"",
        f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **用户输入**: {user_input}",
        f"",
        f"---",
        f"",
        f"## 对话记录",
        f"",
        format_conversation_log(api_messages)
    ]

    # 写入文件
    filepath.write_text("\n".join(content_parts), encoding="utf-8")
    logger.info(f"对话记录已保存: {filepath}")

    return filepath

try:
    from importlib.metadata import version as get_version
    __version__ = get_version("entari_plugin_hyw")
except Exception:
    __version__ = "4.0.0-rc21"

@dataclass
class HywConfig(BasicConfModel):
    """Plugin configuration mapping to HywCoreConfig"""
    # 核心映射字段
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "gpt-4o"
    temperature: float = 0.4

    # 行为配置
    question_command: str = "/q"
    web_command: str = "/w"
    headless: bool = False
    quote: bool = False

    def to_core_config(self) -> HywCoreConfig:
        return HywCoreConfig(
            api_key=self.api_key,
            base_url=self.base_url,
            model_name=self.model_name,
            temperature=self.temperature,
            headless=self.headless
        )

# 初始化插件配置
conf = plugin_config(HywConfig)
history_manager = HistoryManager()

# 初始化核心组件
_core_agent: Optional[HywAgent] = None
_tool_registry: Optional[ToolRegistry] = None

def get_agent():
    global _core_agent, _tool_registry
    if not _core_agent:
        logger.info("Initializing HywCore Agent...")
        core_config = conf.to_core_config()
        _core_agent = HywAgent(core_config)

        # 自动加载工具
        try:
            logger.info(f"Auto-loading tools from hyw_core.tools...")
            _tool_registry = load_tools(core_config)
            # 打印已加载的工具名称以便调试
            if _tool_registry:
                loaded_names = list(_tool_registry._tools.keys())
                logger.info(f"Tools loaded successfully: {loaded_names}")
        except Exception as e:
            logger.exception(f"Failed to load tools: {e}")
            _tool_registry = ToolRegistry()

    return _core_agent, _tool_registry

# === 动态 Policy ===
def standard_flow_policy(session: AgentSession) -> Optional[str]:
    # 示例策略：第一轮如果用户意图模糊，可以注入提示
    if session.turn == 0:
        return (
            "System: 你是一个全能助手。请遵循以下原则：\n"
            "1. **搜索原则**：事实性问题必须优先使用 `web_search`。\n"
            "2. **渲染原则**：对于解释性、总结性、长篇幅或包含列表/代码的回答，**必须**调用 `render_card` 工具生成图片卡片。\n"
            "3. **直接回答**：仅对于简短的闲聊、追问或确认性回复，才可以直接输出文本。\n"
            "4. **禁止询问**：不要问用户是否需要搜索或渲染，请根据上述原则自行判断并行动。"
        )
    return None

# === 指令预处理 ===

@listen(CommandReceive)
async def remove_at(content: MessageChain):
    """
    预处理消息：仅移除开头的 At(@)
    """
    return content.lstrip(At)

# === 指令处理 ===

alc_q = Alconna(conf.question_command, Args["content;?", AllParam])

@command.on(alc_q)
async def handle_question(session: Session[MessageCreatedEvent], result: Arparma):
    content = result.all_matched_args.get("content")

    # 构建 MessageChain，处理引用内容
    mc = MessageChain(content) if content else MessageChain()

    # 处理 Reply：如果引用的不是机器人消息，则追加引用内容
    if session.reply:
        try:
            reply_msg_id = str(session.reply.origin.id) if hasattr(session.reply.origin, 'id') else None
            if reply_msg_id and not history_manager.is_bot_message(reply_msg_id):
                mc.extend(MessageChain(" ") + session.reply.origin.message)
        except Exception:
            mc.extend(MessageChain(" ") + session.reply.origin.message)

    # 提取纯文本
    user_input = str(mc.get(Text)).strip() if mc.get(Text) else ""
    user_input = re.sub(r'<img[^>]+>', '', user_input, flags=re.IGNORECASE)

    if not user_input and not mc.get(Image):
        return

    # 获取历史对话上下文
    hist_key = None
    if session.reply and hasattr(session.reply.origin, 'id'):
        hist_key = history_manager.get_conversation_id(str(session.reply.origin.id))
    hist_payload = history_manager.get_history(hist_key) if hist_key else []

    # 处理图片
    images, _ = await process_images(mc, None)

    agent, registry = get_agent()
    if registry:
        registry.set_send_hook(session.send)

    try:
        runner = FlowRunner(agent, registry)
        result = await runner.run(user_input, flow=chat_flow, images=images)

        final_content = result.content
        should_render = result.should_render
        scoring = result.scoring

        chain = MessageChain()

        if should_render and final_content:
            references = []
            if hasattr(runner, '_session') and runner._session:
                references = extract_references_from_messages(runner._session.api_messages)

            logger.info(f"Rendering card (len={len(final_content)}, refs={len(references)})")
            render_result = await render_card(
                final_content, "Assistant Response",
                headless=conf.headless, references=references, scoring=scoring
            )

            if hasattr(render_result, 'content') and '[IMAGE_BASE64:' in render_result.content:
                match = re.search(r'\[IMAGE_BASE64:\s*([A-Za-z0-9+/=]+)\]', render_result.content)
                if match:
                    chain.append(Image(src=f"data:image/jpeg;base64,{match.group(1)}"))
                else:
                    chain.append(Text(final_content))
            else:
                chain.append(Text(final_content))
        elif final_content:
            chain.append(Text(final_content))

        if chain:
            # 添加 Quote
            if conf.quote:
                chain = MessageChain(Quote(session.event.message.id)) + chain

            sent = await session.send(chain)

            # 记住对话历史
            sent_id = next((str(e.id) for e in sent if hasattr(e, 'id')), None) if sent else None
            msg_id = str(session.event.message.id) if hasattr(session.event, 'message') else str(session.event.id)
            context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"

            updated_history = hist_payload + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": final_content}
            ]
            history_manager.remember(sent_id, updated_history, [msg_id], {}, context_id)

        # 保存对话记录
        if hasattr(runner, '_session') and runner._session:
            save_conversation_log(runner._session.api_messages, user_input)

    except Exception as e:
        logger.exception(f"Error in HywCore execution: {e}")
        await session.send(f"执行出错: {e}")

@listen(Startup)
async def on_startup():
    """Plugin startup hook: initialize agent and warmup browser."""
    logger.info("HywPlugin Startup: Pre-loading agent resources...")

    # 1. 初始化对象 (调用 lazy loader)
    agent, registry = get_agent()

    # 2. 执行异步预热 (浏览器 + 渲染器)
    logger.info("Warming up browser & renderer...")
    await warmup(headless=conf.headless)

    # 打印加载结果确认
    if registry:
        tools = list(registry._tools.keys())
        logger.success(f"HywPlugin Ready! Loaded tools: {tools}")

@listen(Cleanup)
async def cleanup_resources():
    """Clean up browser and agent resources on shutdown."""
    logger.info("Cleaning up HywCore resources...")
    close_shared_browser()

    global _core_agent
    if _core_agent:
        await _core_agent.close()
        _core_agent = None

metadata("hyw", author=[{"name": "kumo", "email": "dev@example.com"}], version=__version__, config=HywConfig)
