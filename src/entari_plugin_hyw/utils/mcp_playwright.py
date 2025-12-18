from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPPlaywright:
    def __init__(
        self,
        command: str = "npx",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        self.command = command
        self.args = args or ["-y", "@playwright/mcp@latest"]
        self.env = env
        self.cwd = cwd

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[ClientSession]:
        server = StdioServerParameters(command=self.command, args=self.args, env=self.env, cwd=self.cwd)
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    @staticmethod
    def mcp_tools_to_openai(tools: Any) -> List[Dict[str, Any]]:
        openai_tools: List[Dict[str, Any]] = []
        for t in tools.tools:
            schema = dict(getattr(t, "inputSchema", None) or {"type": "object", "properties": {}})
            schema.pop("$schema", None)
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": schema,
                    },
                }
            )
        return openai_tools

    @staticmethod
    def call_result_to_text(result: Any) -> str:
        if result is None:
            return ""
        # MCP CallToolResult.content is typically a list of TextContent items
        content = getattr(result, "content", None)
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(str(text))
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(result)


class MCPPlaywrightManager:
    def __init__(
        self,
        command: str = "npx",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        self._client = MCPPlaywright(command=command, args=args, env=env, cwd=cwd)
        self._lock = anyio.Lock()
        self._stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self._tools_openai: Optional[List[Dict[str, Any]]] = None

    async def ensure_connected(self) -> bool:
        async with self._lock:
            if self._session is not None:
                return True

            stack = AsyncExitStack()
            try:
                server = StdioServerParameters(
                    command=self._client.command, args=self._client.args, env=self._client.env, cwd=self._client.cwd
                )
                read, write = await stack.enter_async_context(stdio_client(server))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()

                tools = await session.list_tools()
                self._tools_openai = MCPPlaywright.mcp_tools_to_openai(tools)

                self._stack = stack
                self._session = session
                return True
            except Exception:
                await stack.aclose()
                self._stack = None
                self._session = None
                self._tools_openai = None
                return False

    async def tools_openai(self) -> List[Dict[str, Any]]:
        ok = await self.ensure_connected()
        if not ok or self._tools_openai is None:
            return []
        return self._tools_openai

    async def call_tool_text(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        ok = await self.ensure_connected()
        if not ok or self._session is None:
            return "Error: Playwright MCP is not connected."
        result = await self._session.call_tool(name, arguments or {})
        return MCPPlaywright.call_result_to_text(result)

    async def close(self):
        async with self._lock:
            if self._stack is not None:
                await self._stack.aclose()
            self._stack = None
            self._session = None
            self._tools_openai = None
