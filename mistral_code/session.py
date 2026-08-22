"""Session persistence: save/resume conversation + plan state to disk so a
session survives a restart. Sessions live under a `.mistral-code/` directory
inside the workspace root.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def session_dir(workspace_root: str) -> Path:
    d = Path(workspace_root) / ".mistral-code" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_session_path(workspace_root: str) -> Path:
    return session_dir(workspace_root) / "last.json"


def save_session(path: Path, messages: list[dict[str, Any]], plan_items: list[dict[str, Any]]) -> None:
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "messages": messages,
        "plan": plan_items,
    }
    path.write_text(json.dumps(payload, indent=2))


def load_session(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
