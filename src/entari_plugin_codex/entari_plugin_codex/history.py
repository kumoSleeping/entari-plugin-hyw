import json
import random
import string
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

class HistoryManager:
    def __init__(self, ttl_seconds: int = 7 * 24 * 3600):
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._mapping: Dict[str, str] = {}
        self._context_latest: Dict[str, str] = {}
        self._created_at: Dict[str, float] = {}
        self._related_ids: Dict[str, List[str]] = {}
        self._context_by_key: Dict[str, str] = {}
        self._ttl_seconds: int = max(int(ttl_seconds), 60)
        
        # New: Short code management
        self._short_codes: Dict[str, str] = {} # code -> key
        self._key_to_code: Dict[str, str] = {} # key -> code
        self._context_history: Dict[str, List[str]] = {} # context_id -> list of keys

    def conversation_count(self) -> int:
        return len(self._history)

    def set_ttl_seconds(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(int(ttl_seconds), 60)

    def is_bot_message(self, message_id: str) -> bool:
        """Check if the message ID belongs to a bot message"""
        return message_id in self._history

    def prune_expired(self) -> None:
        """Lazy eviction: only run when new messages are inserted."""
        if not self._created_at:
            return
        now = time.time()
        expired_keys = [key for key, ts in self._created_at.items() if now - ts > self._ttl_seconds]
        if not expired_keys:
            return

        for key in expired_keys:
            # Remove history and metadata
            self._history.pop(key, None)
            self._metadata.pop(key, None)
            self._created_at.pop(key, None)

            # Remove related id mappings
            related_ids = self._related_ids.pop(key, [])
            for rid in related_ids:
                if self._mapping.get(rid) == key:
                    self._mapping.pop(rid, None)

            # Remove short code mappings
            code = self._key_to_code.pop(key, None)
            if code:
                self._short_codes.pop(code, None)

            # Clean context tracking
            context_id = self._context_by_key.pop(key, None)
            if context_id:
                keys = self._context_history.get(context_id, [])
                if keys:
                    self._context_history[context_id] = [k for k in keys if k != key]
                    if not self._context_history[context_id]:
                        self._context_history.pop(context_id, None)
                if self._context_latest.get(context_id) == key:
                    latest = self._context_history.get(context_id, [])
                    if latest:
                        self._context_latest[context_id] = latest[-1]
                    else:
                        self._context_latest.pop(context_id, None)

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

        # Lazy eviction only when inserting new messages
        self.prune_expired()
            
        key = message_id
        self._history[key] = history
        self._created_at[key] = time.time()
        if metadata:
            self._metadata[key] = metadata
            
        self._mapping[key] = key
        for rid in related_ids:
            if rid:
                self._mapping[rid] = key
        self._related_ids[key] = [key] + [rid for rid in related_ids if rid]
                
        # Generate or use provided short code
        if key not in self._key_to_code:
            if not code:
                code = self.generate_short_code()
            self._short_codes[code] = key
            self._key_to_code[key] = code
            
        if context_id:
            self._context_by_key[key] = context_id
            self._context_latest[context_id] = key
            if context_id not in self._context_history:
                self._context_history[context_id] = []
            self._context_history[context_id].append(key)

    def _build_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "saved_at": time.time(),
            "ttl_seconds": int(self._ttl_seconds),
            "history": self._history,
            "metadata": self._metadata,
            "mapping": self._mapping,
            "context_latest": self._context_latest,
            "created_at": self._created_at,
            "related_ids": self._related_ids,
            "context_by_key": self._context_by_key,
            "short_codes": self._short_codes,
            "key_to_code": self._key_to_code,
            "context_history": self._context_history,
        }

    def save_state_file(self, state_file: str) -> bool:
        path = Path(state_file).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._build_state(), ensure_ascii=False, separators=(",", ":"))
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
            return True
        except Exception:
            return False

    def _load_state(self, payload: Dict[str, Any]) -> None:
        history_raw = payload.get("history")
        metadata_raw = payload.get("metadata")
        mapping_raw = payload.get("mapping")
        context_latest_raw = payload.get("context_latest")
        created_at_raw = payload.get("created_at")
        related_ids_raw = payload.get("related_ids")
        context_by_key_raw = payload.get("context_by_key")
        short_codes_raw = payload.get("short_codes")
        key_to_code_raw = payload.get("key_to_code")
        context_history_raw = payload.get("context_history")

        history: Dict[str, List[Dict[str, Any]]] = {}
        if isinstance(history_raw, dict):
            for key, value in history_raw.items():
                key_s = str(key).strip()
                if not key_s or not isinstance(value, list):
                    continue
                rows = [item for item in value if isinstance(item, dict)]
                history[key_s] = rows

        metadata: Dict[str, Dict[str, Any]] = {}
        if isinstance(metadata_raw, dict):
            for key, value in metadata_raw.items():
                key_s = str(key).strip()
                if not key_s or not isinstance(value, dict):
                    continue
                metadata[key_s] = value

        mapping: Dict[str, str] = {}
        if isinstance(mapping_raw, dict):
            for key, value in mapping_raw.items():
                key_s = str(key).strip()
                val_s = str(value).strip()
                if key_s and val_s:
                    mapping[key_s] = val_s

        context_latest: Dict[str, str] = {}
        if isinstance(context_latest_raw, dict):
            for key, value in context_latest_raw.items():
                key_s = str(key).strip()
                val_s = str(value).strip()
                if key_s and val_s:
                    context_latest[key_s] = val_s

        created_at: Dict[str, float] = {}
        if isinstance(created_at_raw, dict):
            for key, value in created_at_raw.items():
                key_s = str(key).strip()
                if not key_s:
                    continue
                try:
                    created_at[key_s] = float(value)
                except (TypeError, ValueError):
                    continue

        related_ids: Dict[str, List[str]] = {}
        if isinstance(related_ids_raw, dict):
            for key, value in related_ids_raw.items():
                key_s = str(key).strip()
                if not key_s or not isinstance(value, list):
                    continue
                related_ids[key_s] = [str(item).strip() for item in value if str(item).strip()]

        context_by_key: Dict[str, str] = {}
        if isinstance(context_by_key_raw, dict):
            for key, value in context_by_key_raw.items():
                key_s = str(key).strip()
                val_s = str(value).strip()
                if key_s and val_s:
                    context_by_key[key_s] = val_s

        short_codes: Dict[str, str] = {}
        if isinstance(short_codes_raw, dict):
            for code, key in short_codes_raw.items():
                code_s = str(code).strip().lower()
                key_s = str(key).strip()
                if code_s and key_s:
                    short_codes[code_s] = key_s

        key_to_code: Dict[str, str] = {}
        if isinstance(key_to_code_raw, dict):
            for key, code in key_to_code_raw.items():
                key_s = str(key).strip()
                code_s = str(code).strip().lower()
                if key_s and code_s:
                    key_to_code[key_s] = code_s

        context_history: Dict[str, List[str]] = {}
        if isinstance(context_history_raw, dict):
            for key, value in context_history_raw.items():
                key_s = str(key).strip()
                if not key_s or not isinstance(value, list):
                    continue
                context_history[key_s] = [str(item).strip() for item in value if str(item).strip()]

        # Rebuild missing maps if state is partial.
        if not mapping:
            for key in history:
                mapping[key] = key
                for rid in related_ids.get(key, []):
                    mapping[rid] = key

        if not key_to_code and short_codes:
            for code, key in short_codes.items():
                key_to_code[key] = code
        elif key_to_code and not short_codes:
            for key, code in key_to_code.items():
                short_codes[code] = key

        # Keep only references to existing conversation keys.
        valid_keys = set(history.keys())
        mapping = {k: v for k, v in mapping.items() if v in valid_keys}
        related_ids = {k: v for k, v in related_ids.items() if k in valid_keys}
        created_at = {k: v for k, v in created_at.items() if k in valid_keys}
        metadata = {k: v for k, v in metadata.items() if k in valid_keys}
        context_by_key = {k: v for k, v in context_by_key.items() if k in valid_keys}
        key_to_code = {k: v for k, v in key_to_code.items() if k in valid_keys}
        short_codes = {code: key for code, key in short_codes.items() if key in valid_keys}

        filtered_context_history: Dict[str, List[str]] = {}
        for context_id, keys in context_history.items():
            hit = [key for key in keys if key in valid_keys]
            if hit:
                filtered_context_history[context_id] = hit
        context_history = filtered_context_history

        filtered_context_latest: Dict[str, str] = {}
        for context_id, key in context_latest.items():
            if key in valid_keys:
                filtered_context_latest[context_id] = key
        context_latest = filtered_context_latest

        self._history = history
        self._metadata = metadata
        self._mapping = mapping
        self._context_latest = context_latest
        self._created_at = created_at
        self._related_ids = related_ids
        self._context_by_key = context_by_key
        self._short_codes = short_codes
        self._key_to_code = key_to_code
        self._context_history = context_history
        self.prune_expired()

    def load_state_file(self, state_file: str) -> bool:
        path = Path(state_file).expanduser()
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return False
            self._load_state(payload)
            return True
        except Exception:
            return False

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
