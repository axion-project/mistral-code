"""Lightweight in-memory plan tracker.

Mirrors the "TodoWrite" pattern from Claude Code: the model externalizes its
plan into a small structured list, which the CLI renders as a checklist so
the user can see progress on multi-step tasks without reading a wall of text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["pending", "in_progress", "completed"]

_STATUS_ICON = {
    "pending": "○",
    "in_progress": "◐",
    "completed": "●",
}


@dataclass
class TodoItem:
    content: str
    status: Status = "pending"


class Plan:
    def __init__(self) -> None:
        self.items: list[TodoItem] = []

    def set_items(self, items: list[dict]) -> None:
        self.items = [
            TodoItem(content=i["content"], status=i.get("status", "pending"))
            for i in items
        ]

    def as_dicts(self) -> list[dict]:
        return [{"content": i.content, "status": i.status} for i in self.items]

    def render(self) -> str:
        if not self.items:
            return "(no active plan)"
        lines = []
        for item in self.items:
            icon = _STATUS_ICON.get(item.status, "○")
            lines.append(f"{icon} {item.content}")
        return "\n".join(lines)
