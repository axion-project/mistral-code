"""Project-level instructions, the same pattern Claude Code uses for
CLAUDE.md: a plain markdown file the model reads on every session so it
knows your conventions without you repeating them in every prompt.

Two locations are checked and, if present, both are concatenated into the
system prompt:

- `~/.mistral-code/MISTRAL.md` — global, applies to every project.
- `<workspace_root>/MISTRAL.md` — project-specific: build/test commands,
  code style, directory layout, things not to touch, etc.

Project instructions are appended after global ones, so a project can
reinforce or add to global conventions. Neither file is required.
"""

from __future__ import annotations

from pathlib import Path

GLOBAL_PATH = Path.home() / ".mistral-code" / "MISTRAL.md"

INIT_TEMPLATE = """\
# MISTRAL.md

Project-specific instructions for mistral-code. This file is read on every
session and appended to the system prompt.

## Build & test

<!-- e.g. `npm run build`, `pytest -q` -->

## Code style

<!-- naming conventions, formatting, patterns to follow or avoid -->

## Structure

<!-- where things live, what talks to what -->

## Don't touch

<!-- generated files, vendored code, anything off-limits -->
"""


def project_path(workspace_root: str) -> Path:
    return Path(workspace_root) / "MISTRAL.md"


def load_context(workspace_root: str) -> str:
    """Read whichever of the global/project MISTRAL.md files exist and
    concatenate them with headers identifying the source. Returns "" if
    neither exists."""
    sections: list[str] = []

    if GLOBAL_PATH.exists():
        try:
            sections.append(f"# Global instructions ({GLOBAL_PATH})\n\n{GLOBAL_PATH.read_text().strip()}")
        except OSError:
            pass

    proj = project_path(workspace_root)
    if proj.exists():
        try:
            sections.append(f"# Project instructions ({proj})\n\n{proj.read_text().strip()}")
        except OSError:
            pass

    return "\n\n---\n\n".join(sections)
