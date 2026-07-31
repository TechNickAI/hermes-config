# Contributing

Thanks for looking. This repo is a starter kit for the
[Hermes Agent](https://hermes-agent.nousresearch.com) — personality presets, a memory
plugin, skills, infrastructure patterns, and the research behind each choice.

## ⚠️ The one rule that matters: zero PII

**This is a public repo. Anyone can clone it. PR descriptions, issue bodies, and commit
history are readable forever.**

Never include in any committed file, PR description, issue body, or commit message:

- Real names of individuals (the copyright line in `LICENSE` is the only exception)
- Names of fleet members, instances, bots, or personas
- Absolute paths under `/Users/<anyone>/...` or `/home/<anyone>/...` — use `~/...`
- LaunchAgent labels with real suffixes
- Port numbers tied to actual running services
- Personal context: family, health, financial, location specifics
- API keys, tokens, bot tokens, chat IDs, phone numbers, IP addresses, hostnames

Explicitly allowed:

- `TechNickAI` in GitHub URLs (public handle)
- `NetworkChuck` and `Jeffrey Canel` as public-figure citations (their public video)
- Generic paths: `~/.hermes/`, `~/.openclaw/`, `~/.config/`
- Public Hermes / OpenClaw source paths and command syntax

### Substitution table

| If you would write...                          | Write instead                                         |
| ---------------------------------------------- | ----------------------------------------------------- |
| A real person's name                           | "the user", "the maintainer", "a partner"             |
| A fleet member, instance, bot, or persona name | `<instance-name>`, `<bot-name>`, "a fleet member"     |
| `/Users/<real>/...`                            | `~/...` or `$HOME/...`                                |
| `~/.openclaw-<real-instance>/`                 | `~/.openclaw-<instance>/`                             |
| `ai.openclaw.<real-instance>`                  | `ai.openclaw.<instance>`                              |
| A real port like `18789`                       | `<gateway-port>`                                      |
| Personal details (family / health / financial) | drop entirely, or generalize to "sensitive context"   |
| A specific incident date                       | "a recent incident" (date specificity rarely matters) |

### Scrub before you stage

```bash
git diff --cached | rg -i 'users/[a-z]+|home/[a-z]+|\.openclaw-[a-z]+|18789|18790|18801'
```

Empty output means safe to commit. Any hits, scrub before staging.

### If you dispatch sub-agents

**Sub-agents do not inherit this repo's context.** A sub-agent reads its prompt and
nothing else. If your prompt omits the PII rule, it will happily write detailed research
full of real paths and personal context and commit it to a public branch. This has
already happened once here.

Copy this into every sub-agent prompt:

> PII rule for this repo: zero PII, zero fleet specifics. Use placeholders for any real
> name, path, port, or personal context. See the substitution table in CONTRIBUTING.md.

Better still, enumerate the specific substitutions the sub-agent will need.

## Development setup

No install step. Clone it and run the tests:

```bash
git clone https://github.com/TechNickAI/hermes-config.git
cd hermes-config

pip install pytest pyyaml pre-commit
pytest -q                                  # 64 Python tests
pre-commit run --all-files                 # linting, matches CI exactly
```

The Node auth-service has its own suite:

```bash
cd devops/app-router/auth-service
npm ci && npm test                         # 18 tests
```

### Tests must pass from a bare clone

This is a hard constraint, not a preference. **Do not require `hermes-agent` to be
installed in order to run the test suite.** A contributor evaluating this repo should be
able to clone and run `pytest` as their first command and see green.

The cortex plugin achieves this through `plugins/memory/cortex/hermes_compat.py`, the
single seam between the two worlds:

- **Inside a Hermes install** — re-exports the real `MemoryProvider`, `tool_error`,
  `cfg_get`, and Hermes-home helpers. `HERMES_RUNTIME` is `True`.
- **From a bare clone** — falls back to behaviour-equivalent stubs whose signatures
  mirror upstream. `HERMES_RUNTIME` is `False`.

If you add code that needs something from the Hermes runtime, add it to `hermes_compat`
with a matching stub. Don't import `agent`, `tools`, `hermes_cli`, or `hermes_constants`
directly anywhere else.

Two more gotchas worth knowing:

- **Hermes ships its own top-level `plugins` package**, which shadows this repo's
  `plugins/` namespace directory. `import plugins.memory.cortex` cannot resolve inside a
  real install regardless of `sys.path` order. Use
  `tests/plugin_loader.load_cortex_plugin()`, which loads by file path the same way
  Hermes does at runtime.
- **`conftest.py` strips ambient `CORTEX_*` and `HERMES_HOME` env vars** for every test.
  Without that, anyone actually running a Hermes agent has their live embedding and
  rerank endpoints leak into the suite — failing assertions about unconfigured defaults,
  and potentially making real network calls during tests.

## Pull requests

- **One coherent concept per PR** — one knowledge doc group, one template set, one
  plugin. Small PRs get reviewed; large ones get stalled.
- All changes land via PR. No direct commits to `main`.
- CI must be green: pre-commit linting, Python tests on 3.11 and 3.13, and Node tests.
- The Claude Code Review action runs on every PR. Mention `@claude` in a comment to
  invoke the agent for follow-up work.

## What belongs here (and what doesn't)

Every addition should survive these five filters:

1. **If Hermes does it natively, this repo does not.** Justify each artifact against a
   built-in.
2. **Lean over comprehensive.** Three good skills beat thirty stale ones. Adding a
   mediocre skill has a real cost.
3. **Skills here are seeds, not the destination.** Hermes' self-improvement loop writes
   better skills than we will, given usage. This is not a marketplace.
4. **Markdown over JSON for anything humans read or edit.**
5. **Public-safe by default.** See the top of this file.

Where things go:

| Content                             | Location                                |
| ----------------------------------- | --------------------------------------- |
| Research, comparisons, deep-dives   | `knowledge/` — lead with the conclusion |
| How-to runbooks and guides          | `docs/`                                 |
| Personality presets                 | `templates/soul/`                       |
| Procedural skills                   | `skills/<name>/SKILL.md`                |
| Hermes plugins                      | `plugins/<category>/<name>/`            |
| Infrastructure patterns and helpers | `devops/`                               |
| Per-machine notes                   | `CLAUDE.local.md` (gitignored)          |

## Working in parallel

Multiple agent sessions can work this repo at once without collisions. The unit of work
is a GitHub issue; `parallel-ready` labels mark issues any session can pick up. See
[`docs/contributing/parallel-work.md`](docs/contributing/parallel-work.md).
