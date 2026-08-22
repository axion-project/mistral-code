# mistral-code

An agentic CLI coding assistant, in the same spirit as Claude Code, powered by
Mistral's models (Codestral or Mistral Large) instead. Point it at a project
directory, describe what you want, and it reads files, edits them, runs
commands, and tracks its own plan as it works.

## Setup

```bash
pip install -e .
export MISTRAL_API_KEY=your-key-here
```

Optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `MISTRAL_MODEL` | `codestral-latest` | Model to use. `mistral-large-latest` is a strong generalist alternative. |
| `MISTRAL_MAX_TOKENS` | `4096` | Max tokens per model response. |
| `MISTRAL_TEMPERATURE` | `0.2` | Sampling temperature. |
| `MISTRAL_MAX_STEPS` | `25` | Ceiling on tool-call round-trips per turn, so a stuck loop can't run forever. |
| `MISTRAL_WORKSPACE` | current directory | Root the agent is confined to. It refuses to read/write outside this path. |
| `MISTRAL_CODE_CONFIRM` | `true` | Set to `false` to skip the diff/command confirmation prompts and run unattended. |

## Run it

```bash
cd your-project
mistral-code
```

Then just talk to it:

```
> add input validation to the signup form and write a test for it
```

In-REPL commands: `/plan`, `/save`, `/resume`, `/init`, `/help`, `/exit`.

## How it works

- **`agent.py`** — the loop. Each turn streams a response from Mistral with
  the tool schemas attached; if the model asks for tool calls, they're
  executed locally and the results fed back in, repeating until the model
  returns plain text (or `MISTRAL_MAX_STEPS` is hit). `edit_file`,
  `write_file`, and destructive `bash` commands are diffed/previewed and
  routed through a confirmation callback before they touch anything.
- **`tools.py`** — `read_file`, `write_file`, `edit_file` (exact-match
  replace, same pattern as Claude Code's edit tool), `list_dir`, `bash`,
  `git_status`/`git_diff`/`git_commit`/`git_log`, `run_subagent`, and
  `todo_write`/`todo_read`. All file paths are resolved and checked against
  the workspace root before any I/O happens.
- **`diff_utils.py`** — computes unified diffs for edit/write previews, and
  a regex heuristic that flags shell commands worth pausing on (`rm -rf`,
  force-pushes, `sudo`, piping a remote script into a shell, etc).
- **`planning.py`** — the todo list the model maintains via `todo_write`,
  rendered as a checklist in the terminal so you can see progress on
  multi-step tasks at a glance.
- **`session.py`** — saves the full message history and plan to
  `.mistral-code/sessions/last.json` after every turn, and offers to resume
  it on the next launch.
- **`mcp_manager.py`** — connects to any MCP servers listed in
  `.mistral-code/mcp.json` over stdio, and merges their tools into the same
  tool-calling loop under `mcp__<server>__<tool>` names.
- **`prompts.py`** — the system prompt: plan-before-acting, read-before-edit,
  prefer surgical edits over full rewrites, verify with tests/bash rather
  than assuming success.
- **`cli.py`** — the `rich`-based REPL: streamed text, tool calls, diff
  previews with a yes/no prompt before anything risky runs, the live plan
  panel, and a `git status` summary in the banner.

## MISTRAL.md — project instructions

Same idea as Claude Code's `CLAUDE.md`: drop a `MISTRAL.md` in your
workspace root and it's read on every session and appended to the system
prompt, so you don't have to repeat your build command, code style, or
"don't touch this directory" rules in every message. Run `/init` in the
REPL to scaffold one with a starter template (build & test, code style,
structure, don't-touch), or write your own.

A second file at `~/.mistral-code/MISTRAL.md` is loaded first as global
instructions that apply across every project, if you keep one — project
instructions load after it, so a project file can extend or override.

## Confirmation prompts

Any `edit_file` or `write_file` call shows you the diff first; any `bash`
call matching the destructive-command heuristic shows you the command
first. Either way you get a yes/no prompt before it actually happens. This
is on by default; set `MISTRAL_CODE_CONFIRM=false` to run unattended (e.g.
in CI or a scripted batch job) — everything still gets logged and diffed
internally, it just isn't gated on a prompt.

## Sub-agents

The model can call `run_subagent(task="...")` to delegate a self-contained
chunk of work to a fresh `Agent` with its own context window. It shares the
same workspace, tools, and confirmation callback, but not the parent's
conversation history — useful for "run the whole test suite and summarize
failures" without filling up the main thread with test output. Sub-agents
can't spawn further sub-agents, so delegation is capped at one level.

## MCP servers

Drop a config at `.mistral-code/mcp.json` in your workspace:

```json
{
  "mcpServers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
  }
}
```

Each server is spawned over stdio at startup and kept alive for the whole
session; its tools show up to the model exactly like the built-in ones,
just prefixed (`mcp__filesystem__read_file`, etc). Connection errors for
one server are reported but don't stop the others from loading.

## Notes

- The `edit_file` tool requires `old_str` to be an *exact, unique* match,
  the same discipline Claude Code uses — combined with the diff-preview
  confirmation, that's what keeps edits safe to apply.
- Tool call arguments arrive as complete JSON per chunk in Mistral's stream
  API (not fragmented like OpenAI's), so no incremental-argument buffering
  is needed in `agent.py`.
- MCP's client API is async; `mcp_manager.py` runs a single background
  thread with its own event loop for the process lifetime and hops onto it
  per call, rather than forcing the whole CLI onto asyncio.
- Both `mistralai` and `mcp` had attribute-naming differences across recent
  versions (e.g. `inputSchema` vs `input_schema`); the code accepts either
  so a `pip install -U` doesn't quietly break tool schemas.
