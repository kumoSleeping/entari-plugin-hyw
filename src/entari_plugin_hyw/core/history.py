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
        import json
        import time
        
        if key not in self._history:
            return

        try:
            os.makedirs(save_dir, exist_ok=True)
            filename = f"{save_dir}/{key}_{int(time.time())}.json"
            
            # Deep copy history to avoid modifying the original in memory
            import copy
            history_to_save = copy.deepcopy(self._history[key])
            
            # Ensure content is JSON serializable or stringified if needed for specific storage requirements
            # But json.dump handles dicts fine. The issue might be when loading back and sending to API.
            # The user's request implies that we should just save it as is, but maybe the API call failed because of it.
            
            data = {
                "key": key,
                "timestamp": time.time(),
                "metadata": self._metadata.get(key, {}),
                "history": history_to_save
            }
            
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            # We can't log easily here without importing logger, but it's fine
            print(f"Failed to save conversation: {e}")
