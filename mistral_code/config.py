"""Configuration for mistral-code.

Reads settings from environment variables, with sane defaults. Keeping this
in one place means swapping models, endpoints, or limits never requires
touching the agent loop itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    api_key: str = field(default_factory=lambda: os.environ.get("MISTRAL_API_KEY", ""))
    # codestral-latest is Mistral's code-tuned model; mistral-large-latest is
    # the strongest generalist. Either works with tool calling.
    model: str = field(default_factory=lambda: os.environ.get("MISTRAL_MODEL", "codestral-latest"))
    max_tokens: int = field(default_factory=lambda: int(os.environ.get("MISTRAL_MAX_TOKENS", "4096")))
    temperature: float = field(default_factory=lambda: float(os.environ.get("MISTRAL_TEMPERATURE", "0.2")))
    # Hard ceiling on agent loop iterations per user turn, so a runaway
    # tool-call loop can't spin forever.
    max_agent_steps: int = field(default_factory=lambda: int(os.environ.get("MISTRAL_MAX_STEPS", "25")))
    # Root directory the agent is allowed to touch. Defaults to cwd so the
    # tool is scoped to the project it was launched in.
    workspace_root: str = field(default_factory=lambda: os.environ.get("MISTRAL_WORKSPACE", os.getcwd()))
    # Show a diff/command preview and ask before edit_file, write_file, or a
    # destructive bash command actually runs. Set MISTRAL_CODE_CONFIRM=false
    # to run unattended (e.g. in CI or a scripted batch job).
    confirm: bool = field(
        default_factory=lambda: os.environ.get("MISTRAL_CODE_CONFIRM", "true").strip().lower()
        not in ("0", "false", "no", "off")
    )

    def validate(self) -> None:
        if not self.api_key:
            raise SystemExit(
                "MISTRAL_API_KEY is not set. Export it before running:\n"
                "  export MISTRAL_API_KEY=your-key-here"
            )
