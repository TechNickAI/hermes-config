# ⚠️ Critical Rules — Read First

These rules apply to every task on this repo and take precedence over everything below.
They exist because not following them produced a real incident on this repo's first day.

## Zero PII, zero fleet specifics

**This is a public repo. Anyone can clone it. Anyone can read PR descriptions, issue
bodies, and commit history forever.**

Never include in any committed file, PR description, issue body, or commit message:

- Real names of individuals (the `Nick Sullivan` in LICENSE for MIT copyright
  attribution is the only exception)
- Names of fleet members, instances, bots, or personas
- Absolute filesystem paths under `/Users/<anyone>/...` — use `~/...` or `$HOME/...`
- LaunchAgent labels with real suffixes (e.g. `ai.openclaw.<real-name>`)
- Port numbers tied to actual running services
- Personal context: family details, health, financial, location specifics
- API keys, tokens, bot tokens, chat IDs, phone numbers, IP addresses, hostnames

Allowed:

- `TechNickAI` in GitHub URLs (public handle)
- `NetworkChuck` and `Jeffrey Canel` as public-figure citations (their public YouTube
  video)
- Generic public paths: `~/.hermes/`, `~/.openclaw/`, `~/.config/`
- Public Hermes / OpenClaw source paths and command syntax

## When dispatching sub-agents (Agent / Task tool)

**Sub-agents do not inherit this repo's context.** The Agent tool spawns an isolated
process that reads its prompt and nothing else. If your prompt does not include the PII
rule, the sub-agent will write detailed research with real paths, fleet member names,
and personal context, then save it to a public branch. That happened on this repo's
first day.

When you invoke the Agent or Task tool, copy this block into the sub-agent prompt
verbatim:

> PII rule for this repo: zero PII, zero fleet specifics. Use placeholders for any real
> name, path, port, or personal context. See substitution table in AGENTS.md.

Better: explicitly enumerate the substitutions the sub-agent might need (see table
below).

## Placeholder substitution table

| If you would write...                          | Write instead                                         |
| ---------------------------------------------- | ----------------------------------------------------- |
| A real person's name                           | "the user", "the maintainer", "a partner"             |
| A fleet member, instance, bot, or persona name | `<instance-name>`, `<bot-name>`, "a fleet member"     |
| `/Users/<real>/...`                            | `~/...` or `$HOME/...`                                |
| `~/.openclaw-<real-instance>/`                 | `~/.openclaw-<instance>/`                             |
| `ai.openclaw.<real-instance>`                  | `ai.openclaw.<instance>`                              |
| Real port like `18789`                         | `<gateway-port>`                                      |
| Personal details (family / health / financial) | drop entirely or generalize to "sensitive context"    |
| Incident dates like `2026-05-10`               | "a recent incident" (date specificity rarely matters) |

## Pre-commit scrub

Before staging anything new, run:

```bash
git diff --cached | rg -i 'users/[a-z]+|\.openclaw-[a-z]+|wife|kids|18789|18790|18801'
```

Empty output → safe to commit. Hits → scrub before staging.

## Reference

The full battle-tested PII playbook lives in the sibling repo's instructions — see
`openclaw-config/CLAUDE.md`. Same posture applies here.

---

# Project Context for AI Assistants

## Project Overview

`hermes-config` is a shareable configuration template for the
[Hermes Agent](https://hermes-agent.nousresearch.com) — a starter kit to bootstrap a
great Hermes setup with curated personas, plugins, skills, and a guided migration from
OpenClaw.

This is the spiritual successor to
[openclaw-config](https://github.com/TechNickAI/openclaw-config). Hermes has built-in
solutions for much of what openclaw-config built from scratch, so this repo is
deliberately leaner.

## Tech Stack

- **Hermes Agent** — the underlying harness (Python, ships with TUI, gateway, plugins,
  MCP, cron)
- **Plugins** at `~/.hermes/plugins/<name>/plugin.yaml` + `__init__.py`
- **Skills** at `~/.hermes/skills/<name>/` (procedural memory — agent-authored markdown)
- **Config** at `~/.hermes/config.yaml`
- **State** at `~/.hermes/state.db` (SQLite with FTS5)
- **Memory** at `~/.hermes/memories/{user.md, memory.md}` with hard char limits +
  optional providers (Honcho, mem0, supermemory)

## Project Structure

- `knowledge/` — Research, comparisons, deep-dives. The "why" behind every decision.
  Read first.
- `docs/` — Migration guide, setup walkthroughs, runbooks, contributor docs
- `templates/` — SOUL.md examples, personality presets _(planned)_
- `plugins/` — Sample Hermes plugins _(planned)_
- `skills/` — Hand-curated procedural skills _(planned)_
- `devops/` — Health checks, machine setup helpers _(planned, scoped down from
  openclaw-config)_

## Code Conventions

- **Public-repo hygiene** — see "Critical Rules" at the top of this file.
- **Markdown over JSON for state.** Hermes (and humans) read and edit markdown
  naturally. JSON is fine for tool output; persistent state files should be markdown.
- **Lean over comprehensive.** If Hermes already does it natively, don't recreate it
  here. Every artifact added to this repo earns its place against a built-in.
- **Skills here are starter kits, not the destination.** Hermes' real skill system is
  the self-improvement loop — the agent writes its own as it learns. This repo seeds a
  few; it does not try to be a marketplace.
- **No `pyproject.toml` at the root.** Hermes plugins use the plugin manifest format;
  sample plugins are self-contained.
- **Migration is a first-class concern.** Hermes ships `hermes claw migrate`; this repo
  documents the surrounding strategy.

## Deployment Model

This repo is a **reference and seed**, not an upstream that pushes to instances. Users:

1. Install Hermes via the official one-liner.
2. Optionally run `hermes claw migrate` if coming from OpenClaw.
3. Copy individual templates, plugins, or skills from this repo into their `~/.hermes/`
   as desired.
4. Read `knowledge/` to understand what to keep, what to drop, and why.

## Git Workflow

- All changes land via pull request after the initial bootstrap. No direct commits to
  `main`.
- Each PR is scoped to one coherent concept (one knowledge doc group, one template set,
  one plugin) so review stays focused.
- The Claude Code Review action runs on every PR
  (`.github/workflows/claude-code-review.yml`).
- Mention `@claude` in a PR or issue comment to invoke the agent for follow-up work.

---

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules protect your context window from
flooding — a single unrouted command can dump 56 KB and waste the session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED

Any Bash command containing `curl` or `wget` is intercepted and replaced with an error.
Use instead:

- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP
  calls in sandbox

### Inline HTTP — BLOCKED

Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`,
`http.get(`, or `http.request(` is intercepted and replaced with an error. Use
`ctx_execute` instead.

### WebFetch — BLOCKED

WebFetch calls are denied entirely. Use `ctx_fetch_and_index(url, source)` then
`ctx_search(queries)`.

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)

Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`,
and other short-output commands. For everything else:
`ctx_batch_execute(commands, queries)` or `ctx_execute(language: "shell", code: "...")`.

### Read (for analysis)

If you are reading a file to **Edit** it → Read is correct. If you are reading to
**analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)`. Only
your printed summary enters context.

### Grep (large results)

Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content.
3. **PROCESSING**: `ctx_execute(language, code)` |
   `ctx_execute_file(path, language, code)`.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)`.
5. **INDEX**: `ctx_index(content, source)` — Store in FTS5 knowledge base.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected
into their prompt. You do NOT need to manually instruct subagents about context-mode.
**However, the PII rule above is NOT auto-injected — copy it into sub-agent prompts
manually.**

## Output constraints

- Keep responses under 500 words.
- Write artifacts to FILES — never inline.
- Use descriptive source labels when indexing so others can
  `ctx_search(source: "label")` later.

## ctx commands

| Command       | Action                                         |
| ------------- | ---------------------------------------------- |
| `ctx stats`   | Call `ctx_stats` and display verbatim          |
| `ctx doctor`  | Call `ctx_doctor`, run returned shell command  |
| `ctx upgrade` | Call `ctx_upgrade`, run returned shell command |
