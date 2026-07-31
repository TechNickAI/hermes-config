<p align="center">
  <img src="https://img.shields.io/badge/Hermes-Config-7F5AF0?style=for-the-badge&labelColor=1a1a2e" alt="Hermes Config">
  <br><br>
  <img src="https://img.shields.io/badge/python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  <a href="https://github.com/TechNickAI/hermes-config/actions/workflows/build.yml"><img src="https://github.com/TechNickAI/hermes-config/actions/workflows/build.yml/badge.svg" alt="Build"></a>
  <a href="https://github.com/TechNickAI/hermes-config/stargazers"><img src="https://img.shields.io/github/stars/TechNickAI/hermes-config?style=flat-square&color=7F5AF0" alt="Stars"></a>
  <a href="https://github.com/TechNickAI/hermes-config/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs Welcome"></a>
</p>

<p align="center">
  <strong>A starter kit and reference architecture for the Hermes Agent.</strong><br>
  Personality presets, a hybrid-retrieval memory plugin, ten battle-tested skills,
  infrastructure patterns, and a researched migration path from OpenClaw.
</p>

---

> [Hermes](https://hermes-agent.nousresearch.com) is an open-source AI agent from
> [NousResearch](https://nousresearch.com) — a Python harness with a built-in TUI,
> messaging gateway, plugin system, MCP support, cron, and a self-improvement loop that
> lets the agent grow its own skills over time. **This repo is a shareable config on top
> of Hermes, not a fork.** Nothing here patches or vendors the agent.

## Why this exists

Hermes gives you a great agent. It does not tell you what to put in it.

The gap between `hermes setup` and _an agent you actually rely on_ is a pile of
decisions: what personality, what memory backend, which skills, what to keep from your
old setup. This repo is one opinionated set of answers, with the research written down
so you can disagree with it on purpose instead of by accident.

Everything here is **take-what-you-want**. There is no install step for the repo itself,
no framework to buy into, and no upstream that pushes to your machine. You copy the
pieces you like into `~/.hermes/` and delete the rest.

## Pick your starting point

| You are...                            | Start here                                                                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| New to Hermes, want a good agent fast | [Quick start](#quick-start) → copy a SOUL preset and a skill or two                                                             |
| Coming from OpenClaw                  | [`knowledge/hermes-vs-openclaw.md`](knowledge/hermes-vs-openclaw.md), then [`docs/migration-guide.md`](docs/migration-guide.md) |
| Here to understand how Hermes works   | [`knowledge/hermes-architecture.md`](knowledge/hermes-architecture.md)                                                          |
| Shopping for a memory backend         | [`knowledge/memory-providers.md`](knowledge/memory-providers.md) — Honcho vs mem0 vs supermemory                                |
| Looking to contribute                 | [Contributing](#contributing) and [`CONTRIBUTING.md`](CONTRIBUTING.md)                                                          |

## Quick start

Install Hermes (this repo assumes you already have it):

```bash
# Linux / macOS / WSL2 / Termux
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
hermes setup
```

Then take what you want from here:

```bash
git clone https://github.com/TechNickAI/hermes-config.git
cd hermes-config

# 1. Give your agent a personality (four presets — see templates/soul/README.md)
cp templates/soul/engineer.md ~/.hermes/SOUL.md

# 2. Add a skill or three (each is a self-contained directory)
cp -r skills/recall ~/.hermes/skills/
cp -r skills/multi-review ~/.hermes/skills/

# 3. Optional: markdown-backed memory with hybrid retrieval
cp -r plugins/memory/cortex ~/.hermes/plugins/
# then set memory.provider: cortex in ~/.hermes/config.yaml
```

Start a session and the changes are live:

```bash
hermes
```

## What's in here

### `templates/` — personality presets

Four `SOUL.md` starters: `personal-assistant`, `engineer`, `it-admin`,
`family-companion`. Hermes loads `~/.hermes/SOUL.md` into every conversation, so this is
the single highest-leverage file in your setup. Copy one, then make it yours. See
[`templates/soul/README.md`](templates/soul/README.md).

### `skills/` — eighteen procedural skills

Skills are markdown procedures the agent loads on demand. These are the ones that earned
their keep in daily use:

| Skill                  | What it does                                                         |
| ---------------------- | -------------------------------------------------------------------- |
| `recall`               | Restore context after `/new` — sessions, memories, transcripts       |
| `keep-going`           | `/keep_going` — restart an agent that stopped short of the goal      |
| `trust-framework`      | Govern your own autonomy: when to act, when to ask, how to earn more |
| `moa-solve`            | Throw multiple models at one hard problem, extract the best answer   |
| `multi-review`         | Review any artifact through a panel of diverse lenses across models  |
| `address-pr-comments`  | Triage PR bot feedback, fix what's valid, push back on what isn't    |
| `pr-review-sweep`      | Nightly sweep of merged PRs for unhandled review comments            |
| `cron-healthcheck`     | Detect broken cron jobs; triage cheap, fix expensive                 |
| `memory-cleanup`       | Shrink a bloated `MEMORY.md` / `USER.md` without losing signal       |
| `mob-check`            | What real people are saying right now — Reddit, X, HN, YouTube       |
| `grok-search`          | Real-time web and X search via xAI's Grok                            |
| `report`               | File a bug or piece of feedback from any platform session            |
| `google-docs`          | Create, format, and export Google Docs from markdown                 |
| `google-sheets`        | Build and populate Sheets from CSV, JSON, or computed tables         |
| `google-slides`        | Markdown to a Slides deck via PPTX conversion and Drive import       |
| `mini-app`             | Add/protect/troubleshoot an app on the mini-app router               |
| `omnirouter`           | Operate a self-hosted multi-provider LLM router                      |
| `recall-from-openclaw` | One-time bridge to find your OpenClaw transcript mid-migration       |

A skill is just a directory — `cp -r` it into `~/.hermes/skills/` and it works.

> **These are seeds, not the destination.** Hermes writes its own skills from successful
> problem-solving. Start with a few good ones and let the agent grow the rest.

### `plugins/memory/cortex/` — markdown knowledge base as memory

A Hermes `MemoryProvider` backed by a directory of markdown pages you can `cd` into,
edit by hand, and version with git. Hybrid retrieval: FTS5/BM25 fused with semantic
embeddings via Reciprocal Rank Fusion, plus an optional cross-encoder rerank tier.
Relevant pages are injected before each turn.

Fails safe by design — no embedding endpoint configured means it quietly degrades to
lexical search rather than breaking recall. See
[`plugins/memory/cortex/README.md`](plugins/memory/cortex/README.md).

### `knowledge/` — the research behind every decision

Ten documents, ~3,000 lines, each leading with its conclusion. This is the part worth
reading even if you take no code:

- **[`hermes-architecture.md`](knowledge/hermes-architecture.md)** — how the harness
  actually fits together
- **[`hermes-vs-openclaw.md`](knowledge/hermes-vs-openclaw.md)** — what transfers, what
  dies, what needs redesign
- **[`memory-deep-dive.md`](knowledge/memory-deep-dive.md)** — three coexisting memory
  layers and why the hard cap is the feature
- **[`memory-providers.md`](knowledge/memory-providers.md)** — Honcho vs mem0 vs
  supermemory, with a recommendation
- **[`skill-system-deep-dive.md`](knowledge/skill-system-deep-dive.md)** — how
  agent-authored skills get promoted and retired
- **[`nousresearch-philosophy.md`](knowledge/nousresearch-philosophy.md)** — "get out of
  the model's way," and what follows from it
- **[`paradigm-translation.md`](knowledge/paradigm-translation.md)** — per-concept
  OpenClaw→Hermes lookup table
- **[`migrator-internals.md`](knowledge/migrator-internals.md)** — what
  `hermes claw migrate` does, step by step
- **[`telegram-and-reactions.md`](knowledge/telegram-and-reactions.md)** — bot handoff
  and reactions, verified end-to-end
- **[`networkchuck-notes.md`](knowledge/networkchuck-notes.md)** — distilled notes incl.
  the NousResearch co-founder interview

### `devops/` — infrastructure patterns

- **`app-router/`** — serve named web apps at clean URLs on one Tailscale HTTPS host,
  with optional per-app passwords. Caddy + PM2 + a small Express auth sidecar (18
  tests).
- **`shared-browser/`** — one login-capable Chrome that multiple agents drive in
  parallel, sharing a cookie jar. One Node daemon and one shell script; no MCP.
- **`migration/`** — audits an OpenClaw→Hermes migration for the failure classes that
  make a migration _look_ complete while still depending on the old tree.

### `docs/` — guides

- **[`migration-guide.md`](docs/migration-guide.md)** — the full OpenClaw→Hermes runbook
- **[`contributing/parallel-work.md`](docs/contributing/parallel-work.md)** — running
  several agent sessions on this repo without collisions

## Migrating from OpenClaw

Hermes ships an importer for your SOUL, memories, skills, allowlists, messaging config,
and API keys:

```bash
hermes claw migrate --dry-run            # preview
hermes claw migrate                       # do it
hermes claw migrate --preset user-data    # without secrets
```

The mechanical part is solved. The strategic part — which paradigms transfer cleanly,
which die quietly, which need a redesign — is what
[`docs/migration-guide.md`](docs/migration-guide.md) and
[`knowledge/paradigm-translation.md`](knowledge/paradigm-translation.md) are for. Read
[`knowledge/hermes-vs-openclaw.md`](knowledge/hermes-vs-openclaw.md) first.

## Design principles

1. **If Hermes does it, this repo does not.** Every artifact justifies itself against a
   built-in.
2. **Lean over comprehensive.** Three good skills beat thirty stale ones.
3. **Agent-authored skills are the destination.** We seed; the agent grows.
4. **Markdown over JSON.** Hermes reads markdown natively. So do humans.
5. **Public-safe by default.** No PII, no fleet specifics. Anyone can clone.

## Development

Everything is testable from a bare clone — no Hermes install required. The cortex plugin
falls back to compatibility stubs when the agent runtime is absent, so the suite runs
anywhere:

```bash
pip install pytest pyyaml
pytest -q                                  # 64 Python tests

cd devops/app-router/auth-service
npm ci && npm test                         # 18 Node tests
```

Linting matches CI exactly:

```bash
pip install pre-commit
pre-commit run --all-files
```

Both suites run on every push and pull request
([`build.yml`](.github/workflows/build.yml)), on Python 3.11 and 3.13.

## Contributing

PRs welcome. Keep each one scoped to a single coherent concept so review stays focused.
Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before your first PR — especially the
**zero-PII rule**, which is non-negotiable in a public repo.

Mention `@claude` in a PR or issue comment to invoke the agent for follow-up work.

## License

MIT. See [LICENSE](LICENSE).
