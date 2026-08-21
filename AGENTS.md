# Project Context for AI Assistants

## ⚠️ Critical rule: zero PII, zero fleet specifics

**This is a public repo.** Never commit real names, fleet member or instance names,
absolute paths under `/Users/<anyone>/` or `/home/<anyone>/`, real port numbers,
personal context, or any credential.

The full rule, the allowed-exceptions list, the placeholder substitution table, and the
pre-commit scrub command live in **[`CONTRIBUTING.md`](CONTRIBUTING.md)**. Read it
before writing anything to disk.

**When dispatching sub-agents:** they do not inherit this context. A sub-agent reads its
prompt and nothing else, so an omitted PII rule means it will write real paths and
personal context onto a public branch — this has already happened here once. Copy this
into every sub-agent prompt:

> PII rule for this repo: zero PII, zero fleet specifics. Use placeholders for any real
> name, path, port, or personal context. See the substitution table in CONTRIBUTING.md.

## If you are here to INSTALL this repo, not contribute to it

Most of this file is about contributing. If your human asked you to _set this up for
them_, you want **[`SETUP.md`](SETUP.md)** instead — it has a complete prompt and the
decision rules. The short version:

1. **Read [`skills/MANIFEST.yaml`](skills/MANIFEST.yaml) first.** It is the generated,
   machine-readable index: `scope`, `requires`, `works_out_of_the_box`, and `use_when`
   for every skill. Do not open nineteen `SKILL.md` files to answer questions this file
   already answers.
2. **Filter by `scope` before anything else.** One machine → install `solo` only.
   `fleet` skills assume multiple hosts, a cron fleet, Caddy/PM2, or a self-hosted LLM
   router; on a laptop they are dead weight.
3. **Install `works_out_of_the_box: true` freely. Everything else is a question.** A
   skill copied without its credential fails silently the first time it is used — verify
   the dependency exists, or tell your human what is missing and skip it.
4. **Never overwrite `~/.hermes/SOUL.md`, `config.yaml`, or `memories/`** without
   showing a diff and getting a yes. Those hold accumulated personal state.
5. **Verify with `./scripts/verify_setup.sh`** and report honestly what you could not
   confirm.

## Project overview

`hermes-config` is a shareable configuration starter kit for the
[Hermes Agent](https://hermes-agent.nousresearch.com): curated personas, a memory
plugin, skills, infrastructure patterns, and a researched migration path from OpenClaw.

It is the spiritual successor to
[openclaw-config](https://github.com/TechNickAI/openclaw-config). Hermes solves natively
much of what openclaw-config built from scratch, so this repo is deliberately leaner.

**This is a reference and seed, not an upstream that pushes to instances.** Users copy
individual templates, plugins, or skills into their own `~/.hermes/`.

## Tech stack

- **Hermes Agent** — the underlying harness (Python 3.13+; TUI, gateway, plugins, MCP,
  cron)
- **Plugins** at `~/.hermes/plugins/<name>/` with `plugin.yaml` + `__init__.py`
- **Skills** at `~/.hermes/skills/<name>/SKILL.md` (procedural memory, agent-authored
  over time)
- **Config** at `~/.hermes/config.yaml`
- **State** at `~/.hermes/state.db` (SQLite with FTS5)
- **Memory** at `~/.hermes/memories/{user.md, memory.md}` — hard char limits, optional
  providers

## Repository structure

| Directory    | Contents                                                                        |
| ------------ | ------------------------------------------------------------------------------- |
| `knowledge/` | Research, comparisons, deep-dives. The "why" behind every decision. Read first. |
| `docs/`      | Migration guide, runbooks, contributor docs                                     |
| `templates/` | SOUL.md personality presets                                                     |
| `plugins/`   | Hermes plugins — currently the cortex memory provider                           |
| `skills/`    | Curated procedural skills                                                       |
| `devops/`    | App router, shared browser, migration audit tooling                             |

## Code conventions

- **Public-repo hygiene** — see the critical rule above.
- **Markdown over JSON for state.** Hermes and humans both read markdown naturally. JSON
  is fine for tool output; persistent state files should be markdown.
- **Lean over comprehensive.** If Hermes does it natively, don't recreate it here.
- **Skills are starter kits, not the destination.** The self-improvement loop writes
  better skills than we will. This repo seeds a few; it is not a marketplace.
- **No `pyproject.toml` at the root.** Plugins use the Hermes plugin manifest format and
  are self-contained.
- **Migration is a first-class concern.** Hermes ships `hermes claw migrate`; this repo
  documents the surrounding strategy.
- **Knowledge docs lead with the conclusion.** State what to do, then why.

## Testing

**Tests must pass from a bare clone — do not require `hermes-agent` to be installed.**

```bash
pytest -q                                                    # 64 Python tests
cd devops/app-router/auth-service && npm ci && npm test       # 18 Node tests
pre-commit run --all-files                                    # linting, matches CI
```

Three things to know before touching plugin code:

1. **`plugins/memory/cortex/hermes_compat.py` is the only place that may import the
   Hermes runtime** (`agent`, `tools`, `hermes_cli`, `hermes_constants`). It re-exports
   the real implementations when Hermes is installed and falls back to signature-matched
   stubs when it isn't. Need something new from the runtime? Add it there with a stub.
2. **Hermes ships its own top-level `plugins` package** that shadows this repo's
   `plugins/` namespace directory, so `import plugins.memory.cortex` cannot resolve
   inside a real install. Use `tests/plugin_loader.load_cortex_plugin()`.
3. **`conftest.py` strips ambient `CORTEX_*` / `HERMES_HOME` env vars** per test, so a
   developer's live endpoints can't leak into assertions or trigger real network calls.

CI (`.github/workflows/build.yml`) runs pre-commit, Python tests on 3.13, and the Node
suite on every push and PR.

## Git workflow

- All changes land via pull request. No direct commits to `main`.
- One coherent concept per PR (one knowledge doc group, one template set, one plugin) so
  review stays focused.
- The Claude Code Review action runs on every PR
  (`.github/workflows/claude-code-review.yml`).
- Mention `@claude` in a PR or issue comment to invoke the agent for follow-up work.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contributor guide and
[`docs/contributing/parallel-work.md`](docs/contributing/parallel-work.md) for running
multiple agent sessions on this repo concurrently.
