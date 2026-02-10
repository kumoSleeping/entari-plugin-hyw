import random
import string
from typing import Dict, List, Any, Optional

class HistoryManager:
    def __init__(self):
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._mapping: Dict[str, str] = {}
        self._context_latest: Dict[str, str] = {}
        
        # New: Short code management
        self._short_codes: Dict[str, str] = {} # code -> key
        self._key_to_code: Dict[str, str] = {} # key -> code
        self._context_history: Dict[str, List[str]] = {} # context_id -> list of keys

    def is_bot_message(self, message_id: str) -> bool:
        """Check if the message ID belongs to a bot message"""
        return message_id in self._history

    def generate_short_code(self) -> str:
        """Generate a unique 4-digit hex code"""
        while True:
            code = ''.join(random.choices(string.hexdigits.lower(), k=4))
            if code not in self._short_codes:
                return code

    def get_conversation_id(self, message_id: str) -> Optional[str]:
        return self._mapping.get(message_id)
        
    def get_key_by_code(self, code: str) -> Optional[str]:
        return self._short_codes.get(code.lower())
        
    def get_code_by_key(self, key: str) -> Optional[str]:
        return self._key_to_code.get(key)

    def get_history(self, key: str) -> List[Dict[str, Any]]:
        return self._history.get(key, [])

    def get_metadata(self, key: str) -> Dict[str, Any]:
        return self._metadata.get(key, {})
        
    def get_latest_from_context(self, context_id: str) -> Optional[str]:
        return self._context_latest.get(context_id)
        
    def list_by_context(self, context_id: str, limit: int = 10) -> List[str]:
        """Return list of keys for a context, most recent first"""
        keys = self._context_history.get(context_id, [])
        return keys[-limit:][::-1]

    def remember(self, message_id: Optional[str], history: List[Dict[str, Any]], related_ids: List[str], metadata: Optional[Dict[str, Any]] = None, context_id: Optional[str] = None, code: Optional[str] = None):
        if not message_id:
            return
            
        key = message_id
        self._history[key] = history
        if metadata:
            self._metadata[key] = metadata
            
        self._mapping[key] = key
        for rid in related_ids:
            if rid:
                self._mapping[rid] = key
                
        # Generate or use provided short code
        if key not in self._key_to_code:
            if not code:
                code = self.generate_short_code()
            self._short_codes[code] = key
            self._key_to_code[key] = code
            
        if context_id:
            self._context_latest[context_id] = key
            if context_id not in self._context_history:
                self._context_history[context_id] = []
            self._context_history[context_id].append(key)

    def save_to_disk(self, key: str, save_root: str = "data/conversations", image_path: Optional[str] = None, web_results: Optional[List[Dict]] = None, vision_trace: Optional[Dict] = None, instruct_traces: Optional[List[Dict]] = None):
        """Save conversation history to specific folder structure"""
        import os
        import time
        import re
        import shutil
        import json
        
        if key not in self._history and not web_results:
            return

        try:
            # Extract user's first message (question) for folder name
            user_question = "unknown_query"
            if key in self._history:
                for msg in self._history[key]:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    user_question = item.get("text", "")
                                    break
                        else:
                            user_question = str(content)
                        break
            
            # Use raw query from first web result if available and no history (for pure search debug)
            if user_question == "unknown_query" and web_results and len(web_results) > 0:
                 q = web_results[0].get("query", "")
                 if q: user_question = q

            # Clean and truncate question
            question_part = re.sub(r'[\\/:*?"<>|\n\r\t]', '', user_question)[:20].strip()
            if not question_part:
                question_part = "conversation"
            
            # Create folder: YYYYMMDD_HHMMSS_question
            time_str = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            folder_name = f"{time_str}_{question_part}"
            
            # Auto-resolve relative paths to absolute if needed
            if not os.path.isabs(save_root):
                 # Try to save next to the project root (assuming we are in src/...)
                 # But safer to just use CWD
                 save_root = os.path.abspath(save_root)
            
            folder_path = os.path.join(save_root, folder_name)
            
            os.makedirs(folder_path, exist_ok=True)
            
            meta = self._metadata.get(key, {})

            # 1. Save Context/Trace
            trace_md = meta.get("trace_markdown")
            if trace_md:
                with open(os.path.join(folder_path, "context_trace.md"), "w", encoding="utf-8") as f:
                    f.write(trace_md)
            
            # 2. Save Web Results (Search & Pages)
            if web_results:
                pages_dir = os.path.join(folder_path, "pages")
                os.makedirs(pages_dir, exist_ok=True)
                
                search_buffer = []  # Buffer for unfetched search results
                
                for i, item in enumerate(web_results):
                    item_type = item.get("_type", "unknown")
                    title = item.get("title", "Untitled")
                    url = item.get("url", "")
                    content = item.get("content", "")
                    item_id = item.get("_id", i + 1)
                    
                    if not content:
                        continue
                    
                    if item_type == "search":
                        # Collect search snippets for consolidated file
                        search_buffer.append(f"## [{item_id}] {title}\n- **URL**: {url}\n\n{content}\n")
                    
                    elif item_type in ["page", "search_raw_page"]:
                        # Save fetched pages/raw search pages individually
                        clean_title = re.sub(r'[\\/:*?"<>|\n\r\t]', '', title)[:30].strip() or "page"
                        filename = f"{item_id:02d}_{item_type}_{clean_title}.md"
                        
                        # Save screenshot if available
                        screenshot_b64 = item.get("screenshot_b64")
                        image_ref = ""
                        if screenshot_b64:
                            try:
                                import base64
                                img_filename = f"{item_id:02d}_{item_type}_{clean_title}.jpg"
                                img_path = os.path.join(pages_dir, img_filename)
                                with open(img_path, "wb") as f:
                                    f.write(base64.b64decode(screenshot_b64))
                                image_ref = f"\n### Screenshot\n![Screenshot]({img_filename})\n"
                            except Exception as e:
                                print(f"Failed to save screenshot for {title}: {e}")
                        
                        page_md = f"# [{item_id}] {title}\n\n"
                        page_md += f"- **Type**: {item_type}\n"
                        page_md += f"- **URL**: {url}\n\n"
                        if image_ref:
                            page_md += f"{image_ref}\n"
                        page_md += f"---\n\n{content}\n"
                        
                        with open(os.path.join(pages_dir, filename), "w", encoding="utf-8") as f:
                            f.write(page_md)
                
                # Save consolidated search results
                if search_buffer:
                    with open(os.path.join(folder_path, "search_results.md"), "w", encoding="utf-8") as f:
                        f.write(f"# Search Results\n\nGenerated at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n" + "\n---\n\n".join(search_buffer))
                
            # 3. Save Final Response (MD)
            final_content = ""
            # Find last assistant message
            for msg in reversed(self._history[key]):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        final_content = content
                    break
            
            if final_content:
                with open(os.path.join(folder_path, "final_response.md"), "w", encoding="utf-8") as f:
                    f.write(final_content)

            # Save Output Image (Final Card)
            if image_path and os.path.exists(image_path):
                try:
                    dest_img_path = os.path.join(folder_path, "output_card.jpg")
                    shutil.copy2(image_path, dest_img_path)
                except Exception as e:
                    print(f"Failed to copy output image: {e}")

            # 4. Save Vision Log (if vision stage was used)
            if vision_trace and not vision_trace.get("skipped"):
                vision_md = "# Vision Stage Log\n\n"
                vision_md += f"- **Model**: {vision_trace.get('model', 'unknown')}\n"
                vision_md += f"- **Time**: {vision_trace.get('time', 0):.2f}s\n"
                vision_md += f"- **Images Count**: {vision_trace.get('images_count', 0)}\n"
                vision_md += f"- **Input Tokens**: {vision_trace.get('usage', {}).get('input_tokens', 0)}\n"
                vision_md += f"- **Output Tokens**: {vision_trace.get('usage', {}).get('output_tokens', 0)}\n\n"
                vision_md += "## Vision Description Output\n\n"
                vision_md += f"```\n{vision_trace.get('output', '')}\n```\n"
                
                with open(os.path.join(folder_path, "vision_log.md"), "w", encoding="utf-8") as f:
                    f.write(vision_md)
            
            # 5. Save Instruct Log (all instruct rounds)
            if instruct_traces:
                instruct_md = "# Instruct Stage Log\n\n"
                for i, trace in enumerate(instruct_traces):
                    stage_name = trace.get("stage_name", f"Round {i+1}")
                    instruct_md += f"## {stage_name}\n\n"
                    instruct_md += f"- **Model**: {trace.get('model', 'unknown')}\n"
                    instruct_md += f"- **Time**: {trace.get('time', 0):.2f}s\n"
                    instruct_md += f"- **Tool Calls**: {trace.get('tool_calls', 0)}\n"
                    instruct_md += f"- **Input Tokens**: {trace.get('usage', {}).get('input_tokens', 0)}\n"
                    instruct_md += f"- **Output Tokens**: {trace.get('usage', {}).get('output_tokens', 0)}\n\n"
                    
                    output = trace.get("output", "")
                    if output:
                        instruct_md += "### Reasoning Output\n\n"
                        instruct_md += f"```\n{output}\n```\n\n"
                    
                    instruct_md += "---\n\n"
                
                with open(os.path.join(folder_path, "instruct_log.md"), "w", encoding="utf-8") as f:
                    f.write(instruct_md)
                
        except Exception as e:
            print(f"Failed to save conversation: {e}")
