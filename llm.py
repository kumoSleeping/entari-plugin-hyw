from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class ChatMessage:
    content: str


@dataclass
class ChatChoice:
    message: ChatMessage


@dataclass
class ChatCompletion:
    choices: List[ChatChoice]
    model: Optional[str] = None


class HttpChatClient:
    """Minimal OpenAI-compatible chat client for /chat/completions."""

    def __init__(self, api_key: Optional[str], base_url: str, timeout: float = 60.0):
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def create_chat_completion(self, **kwargs: Any) -> ChatCompletion:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "entari-plugin-hyw/6.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = await self._client.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=kwargs,
        )
        if response.status_code >= 400:
            raise RuntimeError(_format_error(response))

        payload = response.json()
        choices = []
        for item in payload.get("choices", []):
            message = item.get("message") or {}
            choices.append(ChatChoice(message=ChatMessage(content=message.get("content") or "")))

        if not choices:
            raise RuntimeError(f"LLM response missing choices: {payload}")

        return ChatCompletion(choices=choices, model=payload.get("model"))

    async def close(self):
        await self._client.aclose()


def _format_error(response: httpx.Response) -> str:
    try:
        payload: Dict[str, Any] = response.json()
    except Exception:
        return f"LLM request failed ({response.status_code}): {response.text[:500]}"

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("msg") or str(error)
    else:
        message = str(error or payload)
    return f"LLM request failed ({response.status_code}): {message}"
