"""Entry point: `mistral-code` launches an interactive terminal REPL."""

from __future__ import annotations

import subprocess
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text

from .agent import Agent, TextChunk, ToolCallEvent, TurnDone
from .config import Config
from .mcp_manager import MCPManager
from .memory_files import INIT_TEMPLATE, project_path
from .session import default_session_path, load_session, save_session

console = Console()

BANNER = """[bold cyan]mistral-code[/bold cyan] — an agentic coding assistant for the terminal
Type your request, or [dim]/help[/dim], [dim]/plan[/dim], [dim]/save[/dim], [dim]/resume[/dim], [dim]/init[/dim], [dim]/exit[/dim]."""


def _git_status_line(workspace_root: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--branch"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    branch_line = lines[0].lstrip("#").strip()
    dirty = len(lines) - 1
    suffix = f", {dirty} change{'s' if dirty != 1 else ''}" if dirty else ", clean"
    return f"git: {branch_line}{suffix}"


def _confirm_callback(action: str, target: str, preview: str) -> bool:
    console.print()
    if action in ("edit", "write"):
        lexer = "diff"
        body = preview if len(preview) < 4000 else preview[:4000] + "\n... [truncated]"
        console.print(Panel(Syntax(body, lexer, theme="ansi_dark", word_wrap=True), title=f"{action}: {target}", border_style="yellow"))
    else:
        console.print(Panel(preview, title="about to run", border_style="red"))
    return Confirm.ask(f"[bold yellow]Allow this {action}?[/bold yellow]", default=False)


def _render_tool_call(event: ToolCallEvent) -> None:
    arg_preview = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
    if len(arg_preview) > 100:
        arg_preview = arg_preview[:97] + "..."
    icon = "[red]✗[/red]" if event.declined else "[yellow]→[/yellow]"
    console.print(f"  {icon} [bold]{event.name}[/bold]({arg_preview})")
    style = "red" if event.declined else "dim"
    result_preview = event.result if len(event.result) < 500 else event.result[:500] + "\n  ... [truncated]"
    console.print(Text(result_preview, style=style), soft_wrap=True)


def _maybe_resume(agent: Agent, workspace_root: str) -> None:
    path = default_session_path(workspace_root)
    payload = load_session(path)
    if not payload or not payload.get("messages"):
        return
    saved_at = payload.get("saved_at", "an earlier session")
    if Confirm.ask(f"[cyan]Resume session from {saved_at}?[/cyan]", default=True):
        agent.load_state(payload["messages"], payload.get("plan", []))
        console.print("[dim]session resumed.[/dim]")


def run_repl() -> None:
    config = Config()
    config.validate()

    mcp_manager = MCPManager(config.workspace_root)
    mcp_manager.start()

    agent = Agent(
        config,
        confirm_callback=_confirm_callback if config.confirm else None,
        mcp_manager=mcp_manager,
    )

    console.print(Panel(BANNER, border_style="cyan", expand=False))
    info = f"model: {config.model}  workspace: {config.workspace_root}"
    git_line = _git_status_line(config.workspace_root)
    if git_line:
        info += f"  {git_line}"
    info += f"  confirmations: {'on' if config.confirm else 'off'}"
    console.print(f"[dim]{info}[/dim]")
    if project_path(config.workspace_root).exists():
        console.print("[dim]loaded project instructions from MISTRAL.md[/dim]")
    if mcp_manager.tool_schemas:
        names = ", ".join(sorted({s["function"]["name"].split("__")[1] for s in mcp_manager.tool_schemas}))
        console.print(f"[dim]mcp servers: {names}[/dim]")
    for err in mcp_manager.errors:
        console.print(f"[yellow]mcp warning:[/yellow] {err}")
    console.print()

    _maybe_resume(agent, config.workspace_root)

    try:
        _loop(agent, config)
    finally:
        mcp_manager.stop()


def _loop(agent: Agent, config: Config) -> None:
    while True:
        try:
            user_input = console.input("[bold green]> [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]")
            return

        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            return
        if user_input == "/help":
            console.print(
                "[dim]/plan[/dim]    show the current checklist\n"
                "[dim]/save[/dim]    save this session\n"
                "[dim]/resume[/dim]  load the last saved session\n"
                "[dim]/init[/dim]    scaffold a MISTRAL.md in this workspace\n"
                "[dim]/exit[/dim]    quit"
            )
            continue
        if user_input == "/init":
            p = project_path(config.workspace_root)
            if p.exists():
                console.print(f"[dim]{p} already exists — not overwriting.[/dim]")
            else:
                p.write_text(INIT_TEMPLATE)
                console.print(f"[dim]created {p} — fill it in and it'll load automatically next session.[/dim]")
            continue
        if user_input == "/plan":
            console.print(Panel(agent.plan.render(), title="plan", border_style="magenta"))
            continue
        if user_input == "/save":
            messages, plan_items = agent.export_state()
            save_session(default_session_path(config.workspace_root), messages, plan_items)
            console.print("[dim]session saved.[/dim]")
            continue
        if user_input == "/resume":
            payload = load_session(default_session_path(config.workspace_root))
            if not payload:
                console.print("[dim]no saved session found.[/dim]")
            else:
                agent.load_state(payload["messages"], payload.get("plan", []))
                console.print("[dim]session resumed.[/dim]")
            continue

        console.print()
        wrote_any_text = False
        try:
            for event in agent.run_turn(user_input):
                if isinstance(event, TextChunk):
                    console.print(event.text, end="")
                    wrote_any_text = True
                elif isinstance(event, ToolCallEvent):
                    if wrote_any_text:
                        console.print()
                        wrote_any_text = False
                    _render_tool_call(event)
                elif isinstance(event, TurnDone):
                    console.print()
        except Exception as e:  # noqa: BLE001 - top-level guard so the REPL survives API hiccups
            console.print(f"\n[bold red]error:[/bold red] {e}")

        if agent.plan.items:
            console.print(Panel(agent.plan.render(), title="plan", border_style="magenta"))

        # Auto-save after every turn so a crash or Ctrl-C never loses progress.
        messages, plan_items = agent.export_state()
        save_session(default_session_path(config.workspace_root), messages, plan_items)
        console.print()


def main() -> None:
    try:
        run_repl()
    except SystemExit as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
