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

    def save_to_disk(self, key: str, save_dir: str = "data/conversations"):
        """Save conversation history to disk"""
        import os
        import time
        
        if key not in self._history:
            return

        try:
            os.makedirs(save_dir, exist_ok=True)
            filename = f"{save_dir}/{key}_{int(time.time())}.md"
            
            # Formatter
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            meta = self._metadata.get(key, {})
            model_name = meta.get("model", "unknown")
            code = self._key_to_code.get(key, "N/A")
            
            md_content = f"# Conversation Log: {key}\n\n"
            md_content += f"**Time**: {timestamp}\n"
            md_content += f"**Code**: {code}\n"
            md_content += f"**Model**: {model_name}\n"
            md_content += f"**Metadata**: {meta}\n\n"

            trace_md = meta.get("trace_markdown") if isinstance(meta, dict) else None
            if trace_md:
                md_content += "## Trace\n\n"
                md_content += f"{trace_md}\n\n"

            md_content += "## History\n\n"
            
            for msg in self._history[key]:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                
                md_content += f"### {role}\n\n"
                
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                     import json
                     try:
                         tc_str = json.dumps(tool_calls, ensure_ascii=False, indent=2)
                     except:
                         tc_str = str(tool_calls)
                     md_content += f"**Tool Calls**:\n```json\n{tc_str}\n```\n\n"
                
                # Special handling for tool outputs or complex content
                if role == "TOOL":
                    # Try to pretty print if it's JSON
                    try:
                        import json
                        # Content might be a JSON string already
                        parsed_content = json.loads(content)
                        pretty_content = json.dumps(parsed_content, ensure_ascii=False, indent=2)
                        md_content += f"**Output**:\n```json\n{pretty_content}\n```\n\n"
                    except:
                        md_content += f"**Output**:\n```text\n{content}\n```\n\n"
                else:
                    if content:
                        md_content += f"{content}\n\n"
                
                md_content += "---\n\n"
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(md_content)
                
        except Exception as e:
            # We can't log easily here without importing logger, but it's fine
            print(f"Failed to save conversation: {e}")
