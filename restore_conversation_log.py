import json
import os

file_path = "/Users/kumo/git/10_15/data/conversations/-174023903_1764690509.json"

def restore_log(path):
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    history = data.get("history", [])
    restored_history = []
    
    # We expect a pattern of User -> Assistant -> User -> Assistant
    # If we see two Assistants in a row, we assume a User message was missing between them.
    # Also if the first message is Assistant, we assume a User message was missing before it.
    
    last_role = None
    
    for msg in history:
        role = msg.get("role")
        
        if role == "assistant":
            if last_role == "assistant" or last_role is None:
                # Missing user message before this assistant message
                print("Inserting missing User message placeholder")
                restored_history.append({
                    "role": "user",
                    "content": "[图片]"
                })
        
        restored_history.append(msg)
        last_role = role
        
    data["history"] = restored_history
    print(f"Restored history length: {len(restored_history)}")

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("File saved.")

if __name__ == "__main__":
    restore_log(file_path)
