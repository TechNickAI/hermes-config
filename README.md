<p align="center">
  <img src="https://img.shields.io/badge/Hermes-Config-7F5AF0?style=for-the-badge&labelColor=1a1a2e" alt="Hermes Config">
  <br><br>
  <img src="https://img.shields.io/badge/python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  <a href="https://github.com/TechNickAI/hermes-config/stargazers"><img src="https://img.shields.io/github/stars/TechNickAI/hermes-config?style=flat-square&color=7F5AF0" alt="Stars"></a>
  <a href="https://github.com/TechNickAI/hermes-config/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs Welcome"></a>
</p>

<p align="center">
  <strong>A starter kit and reference architecture for the Hermes Agent.</strong><br>
  Templates, plugins, sample skills, and a guided migration from OpenClaw — opinionated, lean, and built for humans who run their own agents.
</p>

---

> [Hermes](https://hermes-agent.nousresearch.com) is an open-source AI agent from
> [NousResearch](https://nousresearch.com) — a Python harness with a built-in TUI,
> messaging gateway, plugin system, MCP support, cron, and a self-improvement loop that
> lets the agent grow its own skills over time. This repo is a shareable config on top
> of Hermes, not a fork.

## Why this repo exists

If you have been running
[openclaw-config](https://github.com/TechNickAI/openclaw-config), you have shipped piles
of scaffolding to give your agent a persona, memory, skills, workflows, and a fleet.
Hermes does most of that natively. **This repo is the leaner, Hermes-native rewrite** —
the parts of `openclaw-config` worth keeping, the parts that should die, and a clear
migration path for either.

Read `knowledge/hermes-vs-openclaw.md` first if you are migrating.

## What's in here

| Folder       | Purpose                                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `knowledge/` | Research, comparisons, architecture deep-dives. The "why" behind every decision. **Read this first.**                     |
| `templates/` | SOUL.md examples, personality presets, context-file templates _(planned)_                                                 |
| `plugins/`   | Sample Hermes plugins you can drop into `~/.hermes/plugins/` — Honcho, Home Assistant, integrations _(planned)_           |
| `skills/`    | Curated procedural skills (markdown) seeded for new installs. Most skills should be agent-authored over time. _(planned)_ |
| `docs/`      | Migration guide from OpenClaw, setup walkthroughs, runbooks _(planned)_                                                   |
| `devops/`    | Health checks, machine setup, fleet helpers — scoped down from openclaw-config _(planned)_                                |

## Getting started

### New to Hermes

```bash
# Linux / macOS / WSL2 / Termux
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
hermes setup
hermes
```

Then cherry-pick from this repo:

- Copy a `templates/SOUL.md` preset into `~/.hermes/SOUL.md` to set your agent's
  personality
- Drop a `plugins/<name>/` directory into `~/.hermes/plugins/` to add an integration
- Skim `knowledge/hermes-architecture.md` to learn how the pieces fit

### Migrating from OpenClaw

Hermes ships with `hermes claw migrate` — an automatic importer for your SOUL, memories,
skills, allowlists, messaging config, and API keys.

```bash
hermes claw migrate --dry-run            # preview
hermes claw migrate                       # do it
hermes claw migrate --preset user-data    # without secrets
```

Then read `docs/migration-guide.md` _(planned)_ for the **strategic** side of migration:

- Which OpenClaw paradigms transfer cleanly (SOUL, integration skills)
- Which die quietly (BOOT.md, HEARTBEAT.md, the memory tier architecture)
- Which need a redesign (workflows → Hermes cron + skills)
- How to evaluate whether each piece is still worth keeping

See `knowledge/paradigm-translation.md` for the conceptual map.

## Design principles

1. **If Hermes does it, this repo does not.** Every artifact here justifies its
   existence against a built-in.
2. **Lean over comprehensive.** Three good skills beat thirty stale ones.
3. **Agent-authored skills are the destination.** The Hermes self-improvement loop will
   write better skills than we will, given enough usage. We seed; the agent grows.
4. **Markdown over JSON.** Hermes reads markdown natively. So do humans.
5. **Public-safe by default.** No PII, no fleet specifics. Anyone can clone.

## Status

🚧 **Early work in progress** — bootstrap and `knowledge/` are landing first. Templates,
plugins, and migration runbook follow. Watch the
[PR queue](https://github.com/TechNickAI/hermes-config/pulls) to see what's incoming.

## Contributing

PRs welcome. Each PR is kept small (one knowledge doc, one template, one plugin) so
review stays focused. Mention `@claude` in a comment to invoke the agent for follow-up
changes.

## License

MIT. See [LICENSE](LICENSE).
