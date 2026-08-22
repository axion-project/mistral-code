"""MCP client support.

mistral-code can attach external MCP (Model Context Protocol) servers the
same way Claude Code does: read a config file listing servers, connect to
each over stdio, and merge their tools into the local tool-calling loop
under an `mcp__<server>__<tool>` name so they show up to the model exactly
like the built-in tools.

Config lives at `.mistral-code/mcp.json` in the workspace root:

    {
      "mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
      }
    }

MCP's client API is async; the rest of this CLI is sync. Rather than forcing
the whole agent loop onto asyncio, a single background thread runs its own
event loop for the lifetime of the process, and `dispatch` hops onto it via
`run_coroutine_threadsafe`. Each server connection is opened once at startup
and kept alive for the whole session.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any


def config_path(workspace_root: str) -> Path:
    return Path(workspace_root) / ".mistral-code" / "mcp.json"


def _tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


class MCPManager:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.tool_schemas: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._routes: dict[str, tuple[str, Any]] = {}  # tool name -> (server, ClientSession)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None

    @property
    def is_running(self) -> bool:
        return self._loop is not None

    def start(self) -> None:
        """Read the config, connect to every configured server, and populate
        tool_schemas. Safe to call even with no config file (no-op)."""
        cfg_file = config_path(self.workspace_root)
        if not cfg_file.exists():
            return

        try:
            servers = json.loads(cfg_file.read_text()).get("mcpServers", {})
        except (json.JSONDecodeError, OSError) as e:
            self.errors.append(f"could not read {cfg_file}: {e}")
            return
        if not servers:
            return

        ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, args=(servers, ready), daemon=True)
        self._thread.start()
        ready.wait(timeout=30)

    def _run_loop(self, servers: dict[str, Any], ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_all(servers))
        ready.set()
        loop.run_forever()

    async def _connect_all(self, servers: dict[str, Any]) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        for name, spec in servers.items():
            try:
                params = StdioServerParameters(
                    command=spec["command"],
                    args=spec.get("args", []),
                    env=spec.get("env"),
                    cwd=self.workspace_root,
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listing = await session.list_tools()
                for tool in listing.tools:
                    full_name = _tool_name(name, tool.name)
                    self._routes[full_name] = (name, session)
                    # Field name has varied across SDK versions (inputSchema
                    # vs input_schema); accept either.
                    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
                    self.tool_schemas.append(
                        {
                            "type": "function",
                            "function": {
                                "name": full_name,
                                "description": f"[{name}] {tool.description or ''}".strip(),
                                "parameters": schema or {"type": "object", "properties": {}},
                            },
                        }
                    )
            except Exception as e:  # noqa: BLE001 - one bad server shouldn't sink the rest
                self.errors.append(f"{name}: {e}")

    def dispatch(self, name: str, args: dict) -> str:
        if self._loop is None or name not in self._routes:
            return f"Error: unknown MCP tool {name}."
        _, session = self._routes[name]
        tool_name = name.rsplit("__", 1)[1]
        future = asyncio.run_coroutine_threadsafe(session.call_tool(tool_name, args), self._loop)
        try:
            result = future.result(timeout=120)
        except Exception as e:  # noqa: BLE001
            return f"Error calling {name}: {e}"
        parts = []
        for block in result.content:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))
        return "\n".join(parts) if parts else "(empty result)"

    def stop(self) -> None:
        if self._loop is None:
            return
        loop = self._loop

        async def _close():
            if self._stack is not None:
                await self._stack.aclose()

        try:
            asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=10)
        except Exception:  # noqa: BLE001 - best-effort shutdown
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
