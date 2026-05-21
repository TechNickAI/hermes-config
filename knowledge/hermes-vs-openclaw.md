# Hermes vs OpenClaw

> Conclusion first: **Hermes is what OpenClaw wanted to be when it grew up.** Most of
> what `openclaw-config` builds from scratch — memory tiers, skill scaffolding, workflow
> runners, fleet plumbing — Hermes solves natively, and better. This repo exists because
> the parts that _do_ transfer (SOUL, integration patterns, public-repo hygiene,
> migration strategy) still deserve a curated home. The parts that don't transfer should
> die quietly, not be ported.

For per-concept "where does X go now?" mapping, see
[paradigm-translation.md](paradigm-translation.md). For how Hermes actually works under
the hood, see [hermes-architecture.md](hermes-architecture.md).

## The philosophical shift

OpenClaw and Hermes share a goal — give your AI assistant memory, skills, and a persona
it can grow into — but they reach it from opposite directions.

| Dimension                  | OpenClaw                                                       | Hermes                                                                                  |
| -------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Identity**               | A _project_ you maintain                                       | A _product_ you use                                                                     |
| **Default posture**        | Bring your own scaffolding, write your own skills              | Comes with batteries; the agent grows its own skills                                    |
| **Memory philosophy**      | Tiered (always-loaded, daily, vector-indexed) — you curate     | Hard-limited core files (user.md / memory.md), agent curates in the loop                |
| **Skill philosophy**       | Marketplace + hand-written UV scripts                          | Self-improvement loop: agent crystallizes its own from interaction                      |
| **Persona file**           | `SOUL.md` (free-form, templated)                               | `SOUL.md` (free-form, lighter)                                                          |
| **Messaging**              | DIY via channel skills (WhatsApp, iMessage, Telegram, Slack)   | First-class gateway (Telegram, Discord, Slack, WhatsApp, Signal, Email, Home Assistant) |
| **Cron**                   | Plain crontab + cron-runner shim                               | Built-in scheduler with platform delivery                                               |
| **Fleet management**       | Manual SSH over Tailscale, per-machine markdown state          | Profiles + remote terminal backends (docker / ssh / modal / daytona)                    |
| **Sandboxing**             | Trust the host                                                 | Pluggable: local, Docker, SSH, Modal, Daytona, Vercel, Singularity                      |
| **Plugin model**           | UV scripts you install yourself                                | `~/.hermes/plugins/<name>/plugin.yaml` — auto-discovered, hot-swappable                 |
| **MCP**                    | Bring your own                                                 | First-class                                                                             |
| **Update mechanism**       | An `openclaw` skill copies templates/skills from a config repo | Hermes' own installer + `hermes claw migrate`                                           |
| **Failure mode at day 30** | Bloats — files grow, skills accumulate, you babysit            | Self-curates — Curator agent promotes/archives skills, memory stays tight               |
| **Who keeps it healthy**   | You                                                            | The agent (mostly), you (occasionally)                                                  |

OpenClaw asks _"how do I scaffold a great AI assistant?"_ and gives you a toolkit.
Hermes asks _"how do I get out of the model's way?"_ and gives you the result.

## What Hermes does natively that openclaw-config built from scratch

The point of this section is honesty: most of `openclaw-config` would be wasted effort
to port. Here is what dies on contact with Hermes.

### Memory architecture

OpenClaw's three-tier memory (always-loaded `MEMORY.md`, daily `memory/YYYY-MM-DD.md`,
vector-searched `memory/{people,projects,topics}/`) is a thoughtful workaround for the
fact that nothing manages it for you. Hermes replaces all three layers:

- **Always-loaded core**: `~/.hermes/memories/user.md` (hard cap: 1375 chars) and
  `~/.hermes/memories/memory.md` (hard cap: 2200 chars). The hard cap is the whole trick
  — it forces the agent to _delete_ in order to add, which keeps the system prompt tight
  without human babysitting.
- **Daily / session recall**: Hermes stores every session in `~/.hermes/state.db` with
  FTS5 full-text search. There is no "daily file" to maintain because the database
  already answers _"what did we say about X yesterday?"_.
- **Deep knowledge / vector recall**: Plug in [Honcho](https://honcho.ai),
  [mem0](https://mem0.ai), or [supermemory](https://supermemory.ai) via the memory
  provider system. These run as background peer services that build a personality
  profile and surface relevant facts on demand.

A Curator-style background agent periodically fact-checks and prunes your memory file
(around every 10 turns). OpenClaw only runs its equivalent on compact or new session —
Hermes does it _during_ the session, which is why OpenClaw "feels clunky on day 30" and
Hermes does not.

**Implication for hermes-config**: drop the memory tier templates. Document the provider
setup in [memory-deep-dive.md](memory-deep-dive.md) _(in flight)_ and ship a sample
SOUL.md, period.

### Skill system

OpenClaw skills are UV scripts you install ahead of time, with a `SKILL.md` manifest and
an inline-dependency Python entry point. They run as CLI tools the agent invokes.
Maintenance is on you — versioning, deprecation, marketplace hygiene.

Hermes flips this. Skills are **procedural memory** — markdown notes the agent writes
for itself describing _how it solved a hard problem before_. The self-improvement loop
crystallizes a skill after a successful struggle; the Curator then promotes it through
`active → stale → archive` based on whether it keeps being useful. The Hermes team
_does_ ship a curated set of high-quality starter skills (e.g. their internal PR review
skill, distilled from thousands of real reviews), but the intent is that yours grow with
you.

**Implication for hermes-config**: do not try to be a skill marketplace. Ship a small
set of _starter_ skills (the kind you would seed a new install with), plus documentation
of how the self-improvement loop produces better ones naturally. Most users should not
need our skills past day 7.

### Workflows

OpenClaw workflows (`email-steward`, `task-steward`, `calendar-steward`,
`security-sentinel`, etc.) are each a folder with `AGENT.md` (prompt), `rules.md`
(user-owned), `agent_notes.md`, `processed.md`, and `logs/`. Hermes' cron + plugin
system replaces this entire pattern:

- The _scheduling_ is a Hermes cron entry, not a system crontab.
- The _prompt_ is a skill (procedural memory).
- The _state_ goes in the session DB or a plugin's storage.
- The _delivery_ (messaging) is the built-in gateway.

A Hermes cron job is a few lines of config that picks an agent profile, a schedule, and
a skill. You do not need eight markdown files per workflow.

**Implication for hermes-config**: do _not_ port the OpenClaw workflow directories
one-to-one. Instead, ship a handful of `cron/` example entries in the migration guide
showing how `email-steward` → Hermes cron + email plugin + a skill. Fewer files, same
behavior.

### Channel / gateway plumbing

OpenClaw has per-channel skills (Telegram, WhatsApp, iMessage, Slack) — each channel is
its own integration to wire up. Hermes' gateway centralizes Telegram, Discord, Slack,
WhatsApp, Signal, Email, and Home Assistant under `hermes gateway setup` /
`hermes gateway start`. One setup wizard, one running process, multiple platforms.

**Implication for hermes-config**: drop all the per-channel skills as first-class
concepts. Document the gateway in the migration guide. The one gap (business phone
integration) gets a sample plugin if anyone needs it.

### Self-management

The OpenClaw `openclaw` skill handles install, update, status, version checks. Hermes
ships `hermes setup`, `hermes gateway status`, `hermes claw migrate`, `hermes logs`,
`hermes memory setup`, etc. — these are built into the binary.

**Implication for hermes-config**: there is no `hermes-config` skill in this repo.
Hermes already manages itself.

### Fleet management

OpenClaw's per-machine markdown registry + manual SSH-over-Tailscale is a fleet pattern
hand-rolled for power users. Hermes addresses the same problem differently:

- **Profiles** let one Hermes installation host multiple agent personas (one per
  intended user or purpose).
- **Remote terminal backends** (`docker`, `ssh`, `modal`, `daytona`, `vercel`,
  `singularity`) let one agent act on remote machines without needing a Hermes per
  machine.
- **ACP adapter** integrates with VS Code / Zed / JetBrains for editor workflows that
  previously needed separate tooling.

This does not _completely_ replace fleet management for a multi-machine home lab — there
is still real value in "one Hermes per physical box, coordinated centrally" — but it
shrinks the surface area dramatically.

**Implication for hermes-config**: scope down `devops/` to a small set of hardening +
health-check examples. Drop fleet-management-as-a-product.

## What still transfers cleanly from openclaw-config

Not all of `openclaw-config` is dead weight. Things worth keeping:

- **`SOUL.md`** — direct mapping, the file even has the same name. The persona curation
  discipline (durability / uniqueness / retrievability / authority) applies just as
  well.
- **`AGENTS.md` / project context discipline** — Hermes has context files; the habit of
  writing one carefully transfers.
- **Memory write quality criteria** (durability / uniqueness / retrievability /
  authority) — applies to whatever the agent is curating, regardless of where it stores
  it.
- **Integration patterns** — useful as **plugin** examples even if the surface area
  changes.
- **Public-repo hygiene** — zero PII, placeholder convention, `CLAUDE.local.md` for
  fleet specifics. Same rules apply here, verbatim.
- **Markdown-over-JSON for state** — Hermes leans the same way.
- **Health-check spirit** — even with Hermes' built-in robustness, a once-a-day "is the
  gateway still up, are the cron jobs healthy" check is worth keeping.
- **The `knowledge/` discipline itself** — borrowed from `ai-coding-config`, not from
  `openclaw-config`, but it belongs here.

## What needs a real redesign (neither port nor drop)

A few concepts existed in OpenClaw but Hermes does them in a way that requires genuine
rethinking, not a one-to-one mapping.

- **Workflows** — see above. Map to cron + skill + plugin instead of a folder of
  markdown.
- **The OpenClaw self-management skill** — there is no equivalent here, but there could
  be a `hermes-config` _doctor_ command that checks "do you have a memory provider
  running, are your skills curated this month, is your SOUL.md current". Lightweight,
  optional.
- **Fleet management** — see above. Profile + remote-backend first, only hand-roll the
  rest if you actually have a fleet.
- **App-router style auxiliary services** — Hermes does not directly replace this. It is
  essentially Tailscale-served auxiliary services, mostly unrelated to the agent itself.
  Out of scope for `hermes-config`; fork from `openclaw-config` if you need it.

## When the built-in migration handles it (and when it does not)

Hermes ships `hermes claw migrate` which is genuinely good. It handles:

- `SOUL.md`
- Memories (`MEMORY.md`, `USER.md` → `~/.hermes/memories/{memory,user}.md`)
- User-created OpenClaw skills (copied to `~/.hermes/skills/openclaw-imports/`)
- Command allowlist (approval patterns)
- Messaging settings (platform configs, allowed users, working dir)
- Allowlisted API keys (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- TTS workspace audio files
- `AGENTS.md` (with `--workspace-target`)

It does **not** handle:

- Cron jobs (archived to JSON; you re-create via `hermes cron`)
- Workflows (no migration option exists)
- Fleet state files outside the OpenClaw directory
- SSH config

Those gaps are this repo's job — see [migrator-internals.md](migrator-internals.md) _(in
flight)_ for the detailed reverse-engineering, and the migration guide _(planned)_ for
the surrounding strategy.

## This repo's posture

Given all of the above, `hermes-config` should be:

- **A starter kit, not a framework.** Few files, high-signal.
- **Migration-aware, not migration-driven.** Document the strategy, defer the mechanics
  to `hermes claw migrate`.
- **Opinionated about what to drop.** OpenClaw users _will_ try to bring their full
  setup over. This repo's job is to talk them out of it.
- **Anchored in `knowledge/`.** Every template, plugin, and skill we ship should trace
  back to a documented rationale here.

If we are doing this right, `hermes-config` will be a tenth the size of
`openclaw-config` and produce a better day-30 experience.

## Related reading

- [paradigm-translation.md](paradigm-translation.md) — the concrete per-concept map
- [hermes-architecture.md](hermes-architecture.md) — how Hermes actually works
- [memory-deep-dive.md](memory-deep-dive.md) _(in flight)_ — the curator, hard limits,
  providers
- [skill-system-deep-dive.md](skill-system-deep-dive.md) _(in flight)_ — the
  self-improvement loop
- [nousresearch-philosophy.md](nousresearch-philosophy.md) _(in flight)_ — "get out of
  the model's way"
- [networkchuck-notes.md](networkchuck-notes.md) _(in flight)_ — distilled video notes
