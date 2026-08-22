"""Tool implementations and their Mistral function-calling schemas.

Each tool is a plain Python function that returns a string (the result shown
back to the model). Schemas follow the OpenAI-style function-calling format
that Mistral's API also uses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

from .diff_utils import unified_diff
from .planning import Plan

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file's contents, optionally a specific line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the workspace root."},
                    "start_line": {"type": "integer", "description": "1-indexed first line to include."},
                    "end_line": {"type": "integer", "description": "1-indexed last line to include."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing one with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace one exact, unique occurrence of old_str with new_str in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string", "description": "Exact text to find. Must be unique in the file."},
                    "new_str": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and subdirectories at a given path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace root and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds before the command is killed.", "default": 60},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status (porcelain) for the workspace.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff, optionally scoped to a path. Staged diff if staged=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "staged": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage all changes and create a git commit with the given message.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent commit history.",
            "parameters": {
                "type": "object",
                "properties": {"count": {"type": "integer", "default": 10}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_subagent",
            "description": (
                "Delegate a self-contained sub-task to a fresh agent instance with its own "
                "context window, and get back a summary of what it did. Use this for "
                "well-scoped work that would otherwise bloat the main conversation, e.g. "
                "'run the full test suite and summarize failures' or 'audit this directory "
                "for TODO comments and list them'. The sub-agent shares the same workspace "
                "and file/bash/git tools, but not this conversation's history."
            ),
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Replace the current plan with a new checklist. Call this before starting multi-step work and update it as items complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Fetch the current plan/checklist.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class ToolExecutionError(Exception):
    pass


class Tools:
    """Bundles the workspace root and plan state that tool calls act on."""

    def __init__(self, workspace_root: str, plan: Plan):
        self.root = Path(workspace_root).resolve()
        self.plan = plan
        # Set by Agent after construction so the run_subagent tool can spawn
        # a scoped child agent without tools.py importing agent.py (which
        # would create a circular import).
        self.spawn_subagent_fn: Optional[Callable[[str], str]] = None

    def _resolve(self, path: str) -> Path:
        p = (self.root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ToolExecutionError(f"Refusing to access path outside workspace root: {path}")
        return p

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"Error: {path} does not exist."
        lines = p.read_text(errors="replace").splitlines()
        if start_line or end_line:
            start = (start_line or 1) - 1
            end = end_line or len(lines)
            lines = lines[start:end]
            offset = start + 1
        else:
            offset = 1
        return "\n".join(f"{i + offset:>5}\t{line}" for i, line in enumerate(lines))

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} bytes to {path}."

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"Error: {path} does not exist."
        text = p.read_text()
        count = text.count(old_str)
        if count == 0:
            return f"Error: old_str not found in {path}."
        if count > 1:
            return f"Error: old_str matches {count} locations in {path}; make it unique."
        p.write_text(text.replace(old_str, new_str, 1))
        return f"Edited {path}."

    def preview_write(self, path: str, content: str) -> str:
        """Compute the diff a write_file call would produce, without touching disk."""
        p = self._resolve(path)
        before = p.read_text(errors="replace") if p.exists() else ""
        return unified_diff(before, content, path)

    def preview_edit(self, path: str, old_str: str, new_str: str) -> str:
        """Compute the diff an edit_file call would produce, without touching disk."""
        p = self._resolve(path)
        if not p.exists():
            return f"(error: {path} does not exist)"
        before = p.read_text(errors="replace")
        if before.count(old_str) != 1:
            return "(error: old_str is not a unique match; edit will be rejected)"
        return unified_diff(before, before.replace(old_str, new_str, 1), path)

    def list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"Error: {path} does not exist."
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = [f"{'d' if e.is_dir() else 'f'}  {e.name}" for e in entries]
        return "\n".join(lines) if lines else "(empty directory)"

    def bash(self, command: str, timeout: int = 60) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s."
        out = result.stdout.strip()
        err = result.stderr.strip()
        parts = [f"exit code: {result.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)

    def _git(self, *args: str, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                ["git", *args], cwd=self.root, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError:
            return "Error: git is not installed."
        except subprocess.TimeoutExpired:
            return "Error: git command timed out."
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return f"Error (exit {result.returncode}): {err or out}"
        return out or "(no output)"

    def git_status(self) -> str:
        return self._git("status", "--porcelain=v1", "--branch")

    def git_diff(self, path: str | None = None, staged: bool = False) -> str:
        args = ["diff"] + (["--staged"] if staged else [])
        if path:
            args += ["--", path]
        return self._git(*args)

    def git_commit(self, message: str) -> str:
        add_result = self._git("add", "-A")
        if add_result.startswith("Error"):
            return add_result
        return self._git("commit", "-m", message)

    def git_log(self, count: int = 10) -> str:
        return self._git("log", f"-{count}", "--oneline")

    def run_subagent(self, task: str) -> str:
        if self.spawn_subagent_fn is None:
            raise ToolExecutionError("Sub-agents are not available in this context.")
        return self.spawn_subagent_fn(task)

    def todo_write(self, todos: list[dict]) -> str:
        self.plan.set_items(todos)
        return "Plan updated."

    def todo_read(self) -> str:
        return self.plan.render()

    def dispatch(self, name: str, args: dict) -> str:
        fn = getattr(self, name, None)
        if fn is None:
            raise ToolExecutionError(f"Unknown tool: {name}")
        try:
            return fn(**args)
        except ToolExecutionError:
            raise
        except TypeError as e:
            raise ToolExecutionError(f"Bad arguments for {name}: {e}")
