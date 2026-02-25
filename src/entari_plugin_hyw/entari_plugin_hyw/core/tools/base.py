from typing import Dict, Any, List, cast
from openai.types.chat import ChatCompletionToolParam

class ToolDef:
    """Helper to generate OpenAI Tool Schema"""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.properties = {}
        self.required = []

    def add_str(self, name: str, description: str, required: bool = True):
        self.properties[name] = {"type": "string", "description": description}
        if required:
            self.required.append(name)
        return self

    def to_schema(self) -> ChatCompletionToolParam:
        schema = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": self.required
                }
            }
        }
        # Explicitly cast to satisfy Pylance strict typing
        return cast(ChatCompletionToolParam, schema)
