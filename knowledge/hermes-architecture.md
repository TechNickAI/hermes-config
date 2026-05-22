# Hermes Architecture (how the pieces fit)

> Conclusion first: Hermes is a **Python harness** around an agent loop with pluggable
> inference, pluggable memory, pluggable terminal backends, and pluggable platforms.
> Most of what looks like "magic" — the curator agent, the gateway, the cron scheduler,
> the self-improvement loop — is implemented in straightforward Python modules. Reading
> the source is feasible and rewarding; this doc is a guided tour so you don't have to.

This is the structural complement to [hermes-vs-openclaw.md](hermes-vs-openclaw.md)
(philosophy) and [paradigm-translation.md](paradigm-translation.md) (per-concept map).
For the deep dives on specific subsystems, see
[memory-deep-dive.md](memory-deep-dive.md) and
[skill-system-deep-dive.md](skill-system-deep-dive.md) _(in flight)_.

## The agent loop at the center

The core of Hermes is a synchronous loop in `AIAgent.run_conversation()` (in
`run_agent.py`, ~12k LOC). Stripped to its essentials:

```python
while (api_call_count < max_iterations and iteration_budget.remaining > 0) or budget_grace_call:
    if interrupt_requested: break
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

Messages follow OpenAI format (`{"role": "system/user/assistant/tool", ...}`); reasoning
content is stored in `assistant_msg["reasoning"]`. The `AIAgent.__init__` actually takes
~60 parameters in production (credentials, routing, callbacks, session context, budget,
credential pool, etc.) — read the source for the full list.

This loop is the spine. Everything below is a layer wrapped around it.

## Tools and toolsets

`tools/registry.py` exposes
`registry.register(name, toolset, schema, handler, check_fn, requires_env)`. Any
`tools/*.py` file with a top-level `registry.register(...)` call is auto-discovered. A
tool is only _exposed_ to the agent if its name appears in a toolset (see
`toolsets.py`); auto-discovery imports the tool, but `_HERMES_CORE_TOOLS` (and any
platform-specific toolset) is what makes it callable.

Built-in toolsets cover: web search, browser automation, transcription, image
generation, voice/TTS, vision, MCP, skills, kanban, environment execution.

## Terminal backends (where code actually runs)

Hermes can execute shell commands in multiple environments, swappable via
`tools/environments/`:

- `local.py` — your machine
- `docker.py` — containerized
- `ssh.py` — remote machine
- `modal.py` / `managed_modal.py` — Modal cloud
- `daytona.py` — Daytona sandboxes
- `vercel_sandbox.py` — Vercel sandboxes
- `singularity.py` — HPC environments
- `file_sync.py` — for hybrid local/remote workflows

Each backend implements a common interface. Choosing the right backend is a config
decision — not a code change.

## Plugins (the recommended extension path)

Plugins live at `~/.hermes/plugins/<name>/plugin.yaml` + `__init__.py`. They register
tools via `ctx.register_tool(...)` at load time. Plugin toolsets are auto-discovered,
hot-swappable (enable/disable without editing core), and the recommended path for any
custom or local-only tool.

Built-in vs plugin tools differ on one axis: built-in tools require a PR to Hermes core;
plugin tools live in your `~/.hermes/`. For everything that isn't shipping in the base
distribution, use plugins.

Notable plugin areas in the Hermes source tree (`plugins/`):

- `memory/` — Honcho, mem0, supermemory, hindsight (vendor adapters)
- `context_engine/` — context engine plugins
- `model-providers/` — OpenRouter, Anthropic, GMI, etc.
- `kanban/` — multi-agent board dispatcher
- `observability/` — metrics / traces / logs
- `image_gen/` — image generation providers
- Others: `disk-cleanup`, `google_meet`, `platforms`, achievements

## Memory system

Three layers, none of which you maintain by hand:

1. **System prompt files** — `~/.hermes/memories/user.md` (1375-char cap) and
   `~/.hermes/memories/memory.md` (2200-char cap) auto-load on every conversation. Hard
   caps force curation; a background fact-checker prunes around every 10 turns.
2. **Session DB** — `~/.hermes/state.db` is a SQLite database with FTS5 full-text search
   over every session. The agent searches it when relevant.
3. **Memory provider plugin** (optional) — Honcho / mem0 / supermemory run as peer
   services that build a richer model of the user and surface facts on demand.

See [memory-deep-dive.md](memory-deep-dive.md) for the detailed walk-through.

## Skills (procedural memory, not scripts)

Skills are markdown notes the agent writes for itself after solving a hard problem. They
live at `~/.hermes/skills/<name>/`. The self-improvement loop creates them; the Curator
agent promotes them through `active → stale → archive` based on whether they keep being
used.

A separate Skills Hub ships a curated set of high-quality starter skills, distilled by
the Hermes team from real production usage.

See [skill-system-deep-dive.md](skill-system-deep-dive.md) _(in flight)_ for the loop
and lifecycle details.

## Gateway (the messaging layer)

`gateway/` directory implements adapters for Telegram, Discord, Slack, WhatsApp, Signal,
Email, and Home Assistant. One gateway process polls/listens on all configured
platforms, routes messages into agent conversations, and posts replies back.

Per-platform features: typing indicators, lifecycle reactions (👀 on start, 👍/👎 on
completion, clear on `/stop` — enabled via `TELEGRAM_REACTIONS=true`), per-user
allowlists, command approval.

`hermes gateway setup` is the configuration wizard; `hermes gateway start` runs the
process. Lives under a launchd / systemd unit in production.

## TUI + dashboard

`ui-tui/` is the Ink (React) terminal UI you see when you run `hermes`. `tui_gateway/`
is the Python JSON-RPC backend it talks to via stdio.

`hermes dashboard` opens a browser UI (`hermes_cli/web_server.py`) with a `/chat` panel
that embeds the same TUI via a PTY bridge — not a re-implementation. Auxiliary panels
(sidebar, model picker, kanban) are React components but the chat experience itself is
the actual Ink TUI rendered in xterm.js.

## CLI

`cli.py` (`HermesCLI`, ~11k LOC) is the interactive CLI orchestrator. Slash commands are
defined as `CommandDef` entries in `hermes_cli/commands.py` with fields:

- `name` — canonical name without slash
- `description` — human-readable
- `category` — `Session`, `Configuration`, `Tools & Skills`, `Info`, `Exit`
- `aliases` — alternative names
- `args_hint` — argument placeholder shown in help
- `cli_only` / `gateway_only` — scope
- `gateway_config_gate` — config dotpath; when truthy, the command becomes available in
  the gateway

Adding a slash command is a 3-file change: `commands.py` (registry), `cli.py` (handler),
and optionally `gateway/run.py` (gateway handler if exposed there). Aliases are a
one-line tuple addition.

## ACP adapter (editor integration)

`acp_adapter/` is the Agent Coordination Protocol server. VS Code, Zed, and JetBrains
plugins talk to it to drive Hermes from inside the editor. This is the Hermes equivalent
of the "remote agent" patterns OpenClaw users hand-rolled with SSH.

## Cron scheduler

`cron/jobs.py` + `cron/scheduler.py` is the built-in scheduler. A cron job is a
YAML/config entry that picks an agent profile, a schedule, and a skill (or arbitrary
prompt). Hermes manages the process; no system crontab needed.

The scheduler ALSO handles delivery — a job that produces a response can target a
platform (e.g. send the daily briefing to Telegram).

## State, logs, and profiles

- **State**: `~/.hermes/state.db` (sessions, search index)
- **Logs**: `~/.hermes/logs/{agent,errors,gateway}.log`. Browse via
  `hermes logs [--follow] [--level ...] [--session ...]`.
- **Profiles**: one Hermes installation can host multiple personas via
  `~/.hermes/profiles/<name>/`. The CLI and gateway pick a profile via flag or
  environment.

`get_hermes_home()` (in `hermes_constants.py`) is the profile-aware path resolver — used
everywhere paths are referenced, so profile switching just works.

## Config and secrets

- **Config**: `~/.hermes/config.yaml` (settings, model, memory provider, gateway, etc.)
- **Secrets**: `~/.hermes/.env` (API keys only)

`hermes config` is the CLI for reading/writing config. `hermes setup` is the first-time
wizard.

## Session lifecycle

```
hermes setup        # one-time
hermes              # interactive TUI session
hermes gateway      # background messaging gateway
hermes cron         # cron management
hermes memory       # memory inspection / setup
hermes claw migrate # OpenClaw import
hermes logs         # log browsing
hermes dashboard    # browser UI
```

Each command shares the same `AIAgent` core under the hood — same loop, same tools, same
memory.

## How the pieces compose for a typical message

1. **Message arrives** (gateway adapter, CLI input, or cron trigger)
2. **Profile loaded**, system prompt assembled from `SOUL.md` + `user.md` +
   `memory.md` + context files
3. **Memory provider queried** (if configured) — returns relevant facts injected into
   prompt
4. **Conversation history fetched** from `state.db` (recent turns or relevant matches)
5. **Agent loop runs** — model call → tool calls → tool results → repeat → final
   response
6. **Response posted** back to wherever the message came from
7. **Curator (async)** examines the turn, may add/update `memory.md` or write a new
   skill
8. **Session persisted** to `state.db`, available for future search

## When you'd extend each layer

| Want to                      | Touch                                                                    |
| ---------------------------- | ------------------------------------------------------------------------ |
| Add a new tool               | Plugin (`~/.hermes/plugins/<name>/`)                                     |
| Add a new model provider     | Model-providers plugin                                                   |
| Add a new memory provider    | `plugins/memory/<name>/`                                                 |
| Add a new platform / channel | Gateway adapter (likely a PR upstream)                                   |
| Add a new terminal backend   | `tools/environments/<name>.py` (likely a PR upstream)                    |
| Customize startup or persona | `~/.hermes/SOUL.md` + `~/.hermes/memories/user.md`                       |
| Add a scheduled task         | `hermes cron` (no Python needed)                                         |
| Build an editor integration  | ACP adapter (usually nothing to extend; just configure)                  |
| Add a slash command          | If repo-local, a plugin; if upstream, `hermes_cli/commands.py` + handler |

## References

- `~/.hermes/hermes-agent/AGENTS.md` — the Hermes-internal architecture doc (the source
  of most of this content)
- [Hermes docs site](https://hermes-agent.nousresearch.com/docs)
- [hermes-vs-openclaw.md](hermes-vs-openclaw.md) — why this architecture matters
  relative to OpenClaw
- [paradigm-translation.md](paradigm-translation.md) — concrete OpenClaw → Hermes
  mapping
