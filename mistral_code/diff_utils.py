"""Diff previews for file-mutating tools, and a heuristic for flagging shell
commands risky enough to warrant a confirmation prompt before they run.
"""

from __future__ import annotations

import difflib
import re

# Patterns that are hard to walk back: recursive deletes, force pushes,
# history rewrites, permission/ownership changes at scale, disk-level ops,
# database drops, and piping remote scripts straight into a shell.
_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r",
    r"\brm\s+-rf\b",
    r"\bgit\s+push\s+.*--force",
    r"\bgit\s+reset\s+--hard",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    r"\bchmod\s+-R\b",
    r"\bchown\s+-R\b",
    r"\bdrop\s+table\b",
    r"\btruncate\s+table\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"curl[^|]*\|\s*(sudo\s+)?(ba)?sh\b",
    r"wget[^|]*\|\s*(sudo\s+)?(ba)?sh\b",
    r"\bsudo\b",
]
_DESTRUCTIVE_RE = re.compile("|".join(_DESTRUCTIVE_PATTERNS), re.IGNORECASE)


def is_destructive_bash(command: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(command))


def unified_diff(before: str, after: str, path: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    text = "".join(diff)
    return text if text else "(no textual changes)"
