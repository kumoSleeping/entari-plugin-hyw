from arclet.alconna import Alconna, Args, AllParam, CommandMeta, Option, Arparma, MultiVar, store_true
from arclet.entari import Session, command, MessageCreatedEvent, Image, MessageChain
from satori.element import Custom
from loguru import logger
import os
import time
from pathlib import Path

from .. import conf, history_manager, process_request, renderer
from ..utils.misc import resolve_model_name

# Main Command (Question)
alc = Alconna(
    conf.question_command,
    Option("-v|--vision", Args["vision_model", str], help_text="设置视觉模型(设为off禁用)"),
    Option("-t|--text", Args["text_model", str], help_text="设置文本模型"),
    Option("-c|--code", Args["code", str], help_text="继续指定会话"),
    Args["list_models;?", "-m|--models"],
    Args["all_chat;?", "-a"],
    Args["local_mode;?", "-l"],
    Args["all_param?", MultiVar(str | Image | Custom)],
    meta=CommandMeta(
        compact=False, 
        description=f"""使用方法:
        {conf.question_command} -a : 列出所有会话
        {conf.question_command} -m : 列出所有模型
        {conf.question_command} -v <模型名> -t <模型名> : 设置主要视觉模型和文本模型
        {conf.question_command} -l : 开启本地模式 (关闭Web索引)
        {conf.question_command} -c <4位消息码> : 继续指定会话
        {conf.question_command} <问题> : 发起问题
"""
        )
)

@command.on(alc)
async def handle_question_command(session: Session[MessageCreatedEvent], result: Arparma):
    """Handle main Question command"""
    logger.info(f"Question Command Triggered. Message: {session.event.message}")
    
    args = result.all_matched_args
    logger.info(f"Matched Args: {args}")
    
    text_model_val = args.get("text_model")
    vision_model_val = args.get("vision_model")
    code_val = args.get("code")
    all_flag_val = args.get("all_chat")
    list_models_val = args.get("list_models")
    local_mode_val = True if args.get("local_mode") else False
    logger.info(f"Local mode: {local_mode_val} (type: {type(local_mode_val)})")
    
    # Handle -m (List Models)
    if list_models_val:
        from .. import global_cache
        
        if global_cache.models_image_path and os.path.exists(global_cache.models_image_path):
            logger.info(f"Using cached models list: {global_cache.models_image_path}")
            await session.send(MessageChain(Image.of(path=Path(global_cache.models_image_path).absolute())))
            return

        output_dir = "data/cache"
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/models_list_cache.png"
        
        await renderer.render_models_list(conf.models, output_path, default_base_url=conf.base_url)
        global_cache.models_image_path = os.path.abspath(output_path)
        
        await session.send(MessageChain(Image.of(path=Path(output_path).absolute())))
        return
    
    # Handle -a (List History)
    if all_flag_val:
        context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"
        keys = history_manager.list_by_context(context_id, limit=10)
        if not keys:
            await session.send("暂无历史会话")
            return
            
        msg = "历史会话 [最近10条]\n"
        for i, key in enumerate(keys):
            short_code = history_manager.get_code_by_key(key) or "????"
            hist = history_manager.get_history(key)
            preview = "..."
            if hist and len(hist) > 0:
                last_content = hist[-1].get("content", "")
                preview = (last_content[:20] + "...") if len(last_content) > 20 else last_content
            
            msg += f"{short_code} {preview}\n"
        await session.send(msg)
        return
    
    selected_vision_model = None
    selected_text_model = None
    
    if vision_model_val:
        if vision_model_val.lower() == "off":
            selected_vision_model = "off"
        else:
            selected_vision_model, err = resolve_model_name(vision_model_val, conf.models)
            if err:
                await session.send(err)
                return
        logger.info(f"Selected vision model: {selected_vision_model}")
        
    if text_model_val:
        selected_text_model, err = resolve_model_name(text_model_val, conf.models)
        if err:
            await session.send(err)
            return
        logger.info(f"Selected text model: {selected_text_model}")
        
    # Determine History to Continue
    target_key = None
    context_id = f"guild_{session.guild.id}" if session.guild else f"user_{session.user.id}"
    
    # 1. Explicit Code
    if code_val:
        target_code = code_val
        target_key = history_manager.get_key_by_code(target_code)
        if not target_key:
            await session.send(f"未找到代码为 {target_code} 的会话")
            return
        logger.info(f"Question: Continuing session {target_code} -> {target_key}")
                
    await process_request(session, args.get("all_param"), selected_model=selected_text_model, selected_vision_model=selected_vision_model, conversation_key_override=target_key, local_mode=local_mode_val)
