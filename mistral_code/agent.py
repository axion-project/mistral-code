"""The agent loop.

Each user turn: send the conversation + tool schemas to Mistral. If the
model responds with tool calls, execute them locally and feed the results
back as tool messages, then call the model again. Repeat until the model
returns a plain text response, or we hit the step ceiling.

This is a generator so the CLI can render progress (tool calls, results,
streamed text) as it happens rather than waiting for the whole turn to
finish.

Two things happen before a risky tool call is actually dispatched:
- `edit_file` / `write_file` / destructive `bash` commands are previewed
  (a diff, or the raw command) and passed through `confirm_callback` if one
  is set. Declining short-circuits the call with a message the model sees.
- MCP tool calls (`mcp__<server>__<tool>`) are routed to the MCPManager
  instead of the local Tools instance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

try:
    from mistralai import Mistral
except ImportError:  # pragma: no cover - covers SDK layouts that nest the client module
    from mistralai.client import Mistral

from .config import Config
from .diff_utils import is_destructive_bash
from .memory_files import load_context
from .planning import Plan
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, Tools, ToolExecutionError

ConfirmCallback = Callable[[str, str, str], bool]  # (action, target, preview) -> approved?


@dataclass
class ToolCallEvent:
    name: str
    args: dict
    result: str
    declined: bool = False


@dataclass
class TextChunk:
    text: str


@dataclass
class TurnDone:
    pass


AgentEvent = ToolCallEvent | TextChunk | TurnDone

_DIRTY_TOOLS = {"edit_file", "write_file", "bash"}


class Agent:
    def __init__(
        self,
        config: Config,
        confirm_callback: Optional[ConfirmCallback] = None,
        mcp_manager: Optional[Any] = None,
        is_subagent: bool = False,
    ):
        self.config = config
        self.client = Mistral(api_key=config.api_key)
        self.plan = Plan()
        self.tools = Tools(config.workspace_root, self.plan)
        self.confirm_callback = confirm_callback
        self.mcp_manager = mcp_manager
        self.is_subagent = is_subagent

        system_content = SYSTEM_PROMPT
        memory = load_context(config.workspace_root)
        if memory:
            system_content += "\n\n" + memory
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

        self.tool_schemas = list(TOOL_SCHEMAS)
        if is_subagent:
            # A sub-agent can't spawn further sub-agents; keeps delegation
            # to one level and avoids unbounded fan-out.
            self.tool_schemas = [s for s in self.tool_schemas if s["function"]["name"] != "run_subagent"]
        else:
            self.tools.spawn_subagent_fn = self._run_subagent
        if mcp_manager is not None:
            self.tool_schemas = self.tool_schemas + mcp_manager.tool_schemas

    # -- sub-agents ---------------------------------------------------

    def _run_subagent(self, task: str) -> str:
        child = Agent(
            self.config,
            confirm_callback=self.confirm_callback,
            mcp_manager=self.mcp_manager,
            is_subagent=True,
        )
        text_parts: list[str] = []
        for event in child.run_turn(task):
            if isinstance(event, TextChunk):
                text_parts.append(event.text)
        return "".join(text_parts).strip() or "(sub-agent produced no output)"

    # -- session persistence -------------------------------------------

    def export_state(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return self.messages, self.plan.as_dicts()

    def load_state(self, messages: list[dict[str, Any]], plan_items: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.plan.set_items(plan_items)

    # -- confirmation gate for risky tool calls -------------------------

    def _confirm(self, name: str, args: dict) -> tuple[bool, str]:
        """Returns (approved, preview_text). Tools not requiring confirmation
        are always approved with an empty preview."""
        if name == "edit_file":
            preview = self.tools.preview_edit(args.get("path", ""), args.get("old_str", ""), args.get("new_str", ""))
            action, target = "edit", args.get("path", "")
        elif name == "write_file":
            preview = self.tools.preview_write(args.get("path", ""), args.get("content", ""))
            action, target = "write", args.get("path", "")
        elif name == "bash" and is_destructive_bash(args.get("command", "")):
            preview = args.get("command", "")
            action, target = "run", "a potentially destructive command"
        else:
            return True, ""

        if self.confirm_callback is None:
            return True, preview
        return self.confirm_callback(action, target, preview), preview

    # -- the loop ---------------------------------------------------------

    def run_turn(self, user_input: str) -> Iterator[AgentEvent]:
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.config.max_agent_steps):
            stream = self.client.chat.stream(
                model=self.config.model,
                messages=self.messages,
                tools=self.tool_schemas,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )

            text_parts: list[str] = []
            tool_calls: list[Any] = []

            for chunk in stream:
                delta = chunk.data.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextChunk(delta.content)
                if delta.tool_calls:
                    tool_calls.extend(delta.tool_calls)

            assistant_content = "".join(text_parts)
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": assistant_content or None}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]
            self.messages.append(assistant_msg)

            if not tool_calls:
                yield TurnDone()
                return

            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                name = tc.function.name
                declined = False

                if name.startswith("mcp__"):
                    if self.mcp_manager is None:
                        result = f"Error: no MCP manager available for {name}."
                    else:
                        result = self.mcp_manager.dispatch(name, args)
                elif name in _DIRTY_TOOLS:
                    approved, preview = self._confirm(name, args)
                    if not approved:
                        result = "Declined by user; no changes were made."
                        declined = True
                    else:
                        try:
                            result = self.tools.dispatch(name, args)
                        except ToolExecutionError as e:
                            result = f"Error: {e}"
                else:
                    try:
                        result = self.tools.dispatch(name, args)
                    except ToolExecutionError as e:
                        result = f"Error: {e}"

                yield ToolCallEvent(name=name, args=args, result=result, declined=declined)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": result,
                    }
                )

        yield TextChunk(
            "\n[stopped: hit max_agent_steps without finishing — ask me to continue if needed]"
        )
        yield TurnDone()
