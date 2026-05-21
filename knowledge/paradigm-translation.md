# Paradigm Translation: OpenClaw → Hermes

> Conclusion first: most OpenClaw concepts have a Hermes equivalent, but the equivalence
> is rarely 1:1. This doc is the per-concept lookup — what to **port** as-is, what to
> **redesign** because Hermes' shape is different, and what to **drop** because Hermes
> already does it natively. Use the checklist at the bottom when actually migrating an
> instance.

For the architectural _why_ behind these calls, see
[hermes-vs-openclaw.md](hermes-vs-openclaw.md). For the actual mechanics of running a
migration, see the migration guide _(planned)_.

## Legend

- 🟢 **Port** — direct or near-direct mapping; copy with minor edits
- 🟡 **Redesign** — concept transfers, but the Hermes shape is different enough that
  copy-paste won't work
- 🔴 **Drop** — Hermes does this natively or makes it unnecessary
- ⚪ **Out of scope** — neither port nor redesign; not a `hermes-config` concern

## Identity files

| OpenClaw                | Hermes                                           | Action      | Notes                                                                                                                                             |
| ----------------------- | ------------------------------------------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SOUL.md`               | `~/.hermes/SOUL.md`                              | 🟢 Port     | Same file, same purpose. Just copy. `hermes claw migrate` does this for you.                                                                      |
| `USER.md`               | `~/.hermes/memories/user.md` (cap: 1375 chars)   | 🟡 Redesign | Trim to fit. The hard cap is the feature — let the curator agent take over after that.                                                            |
| `MEMORY.md`             | `~/.hermes/memories/memory.md` (cap: 2200 chars) | 🟡 Redesign | Same as USER. Don't try to preserve everything; let it self-curate.                                                                               |
| `IDENTITY.md`           | —                                                | 🔴 Drop     | OpenClaw scaffolding; the persona lives in `SOUL.md` in Hermes.                                                                                   |
| `BOOT.md`               | —                                                | 🔴 Drop     | Hermes' startup is built into the binary; no markdown startup routine to maintain.                                                                |
| `HEARTBEAT.md`          | —                                                | 🔴 Drop     | Replace with Hermes cron entries for periodic checks.                                                                                             |
| `TOOLS.md`              | `~/.hermes/config.yaml` + `~/.hermes/.env`       | 🟡 Redesign | Tool inventory was OpenClaw discovery; Hermes uses config + env. Migrate values, drop the file format.                                            |
| `AGENTS.md` (workspace) | `<target>/AGENTS.md` via `--workspace-target`    | 🟢 Port     | Hermes calls them "context files". `hermes claw migrate --workspace-target <dir>` writes `AGENTS.md` into the directory you specify (no default). |

## Memory

| OpenClaw                                                                  | Hermes                                                         | Action      | Notes                                                                                          |
| ------------------------------------------------------------------------- | -------------------------------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------- |
| Tier 1: `MEMORY.md` always loaded                                         | `~/.hermes/memories/memory.md` (hard char limit, auto-curated) | 🟡 Redesign | Smaller, sharper, self-managed.                                                                |
| Tier 2: `memory/YYYY-MM-DD.md` daily                                      | `~/.hermes/state.db` (SQLite + FTS5 session search)            | 🔴 Drop     | Hermes' session DB answers "what did we say yesterday" without you maintaining daily files.    |
| Tier 3: `memory/people/`, `projects/`, `topics/`                          | Memory provider plugin (Honcho, mem0, supermemory)             | 🟡 Redesign | The _knowledge_ transfers, but the storage moves to a peer service. See `memory-deep-dive.md`. |
| Vector embeddings via separately-run embedding server                     | Memory provider handles embeddings internally                  | 🔴 Drop     | No need to run your own embedding server unless you're self-hosting Honcho/mem0.               |
| `cortex` skill (raw → structured knowledge)                               | Memory provider does this in the background                    | 🟡 Redesign | Honcho builds personality profiles automatically; mem0 has its own extraction.                 |
| Memory curation criteria (durability/uniqueness/retrievability/authority) | Same criteria, applied by curator agent + provider             | 🟢 Port     | The discipline transfers even though the storage doesn't.                                      |

## Skills

| OpenClaw                                                  | Hermes                                                             | Action          | Notes                                                                                                                              |
| --------------------------------------------------------- | ------------------------------------------------------------------ | --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Skills as UV scripts at `skills/<name>/<name>` + SKILL.md | `~/.hermes/skills/<name>/` (markdown procedural memory)            | 🟡 Redesign     | Drastically different shape. UV scripts → markdown notes. `claw migrate` parks the originals in `openclaw-imports/` for reference. |
| Skill marketplace                                         | Curated Skills Hub + agent-authored skills (self-improvement loop) | 🔴 Drop         | Don't recreate a marketplace. The agent grows its own.                                                                             |
| OpenClaw self-management skill                            | `hermes setup`, `hermes gateway`, `hermes claw migrate`, etc.      | 🔴 Drop         | Hermes manages itself.                                                                                                             |
| `workflow-builder` skill                                  | Write a skill prompt directly (markdown)                           | 🔴 Drop         | The workflow concept itself collapses (see Workflows row).                                                                         |
| `smart-delegation` skill                                  | Hermes' built-in subagent / model selection                        | 🔴 Drop         | First-class in Hermes.                                                                                                             |
| `create-great-prompts` skill                              | A markdown skill or a context file                                 | 🟢 Port         | Useful as a Hermes skill; convert from UV script to markdown procedure.                                                            |
| `review` skill                                            | Skills Hub already ships an internal PR review skill               | 🔴 Drop         | Hermes' built-in (from real review distillation) is better.                                                                        |
| Web search skill                                          | Hermes pluggable search providers                                  | 🟡 Redesign     | Search providers are already pluggable in Hermes. Configure via `hermes tools` instead of a skill.                                 |
| Transcript / meeting / pendant skills                     | Hermes plugins                                                     | 🟡 Redesign     | Convert each to a `~/.hermes/plugins/<name>/` directory.                                                                           |
| Photo / media skills                                      | Hermes plugins                                                     | 🟡 Redesign     | Same — plugin pattern.                                                                                                             |
| Email skill                                               | Hermes plugin                                                      | 🟡 Redesign     |                                                                                                                                    |
| CRM skills                                                | Hermes plugins (or MCP integration — check first)                  | 🟡 Redesign     | If an MCP server exists for the service, prefer that path.                                                                         |
| Voice / call skills                                       | Hermes plugin                                                      | 🟡 Redesign     |                                                                                                                                    |
| Generic productivity skill                                | Markdown skill                                                     | 🟢 Port         | Pure procedural; converts cleanly.                                                                                                 |
| Gateway-restart skill                                     | `hermes gateway restart`                                           | 🔴 Drop         | Built-in.                                                                                                                          |
| Claude Code skill                                         | Already in Claude Code; not a Hermes concern                       | ⚪ Out of scope |                                                                                                                                    |

## Workflows

| OpenClaw                                                                            | Hermes                                                                | Action      | Notes                                                                                |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------ |
| Workflow folder (`AGENT.md`, `rules.md`, `agent_notes.md`, `processed.md`, `logs/`) | Hermes cron entry + skill + plugin for state                          | 🟡 Redesign | The folder-of-markdown pattern dissolves. See examples below per workflow type.      |
| Email steward                                                                       | Cron entry calling an email-skill, email plugin for IMAP/labels       | 🟡 Redesign | Drop the rules/notes/processed cycle; let the Curator + memory provider track state. |
| Task steward                                                                        | Cron + task-management plugin (e.g. Asana / Todoist via MCP)          | 🟡 Redesign |                                                                                      |
| Calendar steward                                                                    | Cron + calendar plugin (Google Calendar via MCP)                      | 🟡 Redesign |                                                                                      |
| Contact steward                                                                     | Cron + memory provider (it's already deduplicating people)            | 🟡 Redesign |                                                                                      |
| Security sentinel                                                                   | Cron + a security-skill                                               | 🟡 Redesign |                                                                                      |
| Cron healthcheck                                                                    | Cron job that probes the gateway + writes to a memory note on failure | 🟡 Redesign | Simpler than the OpenClaw scaffolding once you have native cron.                     |
| Learning loop                                                                       | Curator agent does this natively                                      | 🔴 Drop     | The self-improvement loop _is_ the learning loop.                                    |

## Channels / Gateway

| OpenClaw                | Hermes                                      | Action      | Notes                                                                                            |
| ----------------------- | ------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------ |
| WhatsApp channel skill  | `hermes gateway` (WhatsApp built-in)        | 🔴 Drop     | Native.                                                                                          |
| iMessage channel        | `hermes gateway` (no native iMessage today) | 🟡 Redesign | If you need iMessage, you'll need a plugin or to keep an OpenClaw bridge running. Open question. |
| Telegram channel        | `hermes gateway` (Telegram built-in)        | 🔴 Drop     | Native. See `telegram-and-reactions.md` _(in flight)_ for token-handoff guidance.                |
| Slack channel           | `hermes gateway` (Slack built-in)           | 🔴 Drop     | Native.                                                                                          |
| Telegram client tooling | `hermes gateway` + bot API                  | 🔴 Drop     | Mostly redundant.                                                                                |

## Cron / scheduling

| OpenClaw                            | Hermes                                       | Action      | Notes                                                                                               |
| ----------------------------------- | -------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------- |
| System crontab + an OpenClaw runner | Hermes built-in scheduler (`cron/jobs.py`)   | 🟡 Redesign | Move jobs into Hermes' format. `hermes claw migrate` archives them as JSON; you re-create manually. |
| Per-job model/provider selection    | Hermes cron supports model selection per job | 🟢 Port     | Concept transfers; syntax changes.                                                                  |
| Per-job timeout                     | Same in Hermes                               | 🟢 Port     |                                                                                                     |
| Cron error notification routing     | Hermes cron has platform delivery built in   | 🔴 Drop     | Native.                                                                                             |

## Fleet management

| OpenClaw                               | Hermes                                                  | Action      | Notes                                                                                  |
| -------------------------------------- | ------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------- |
| Per-machine markdown registry          | Profiles + remote terminal backends                     | 🟡 Redesign | Most fleet ops shrink dramatically. What remains is a small "machines I run" registry. |
| Manual SSH over Tailscale              | Hermes `ssh` terminal backend; one agent acts on many   | 🟡 Redesign | Fewer Hermes installs, more reach per install.                                         |
| Shared embedding server over Tailscale | Each Hermes uses its memory provider locally            | 🔴 Drop     | No more shared embedding server.                                                       |
| Cron-based fleet healthchecks          | Hermes cron + a fleet plugin                            | 🟡 Redesign | Lighter weight.                                                                        |
| `/fleet` slash command                 | `/fleet` slash command in Hermes (custom or via plugin) | 🟡 Redesign | Worth porting if you keep a multi-machine setup.                                       |

## DevOps

| OpenClaw                                   | Hermes                                          | Action          | Notes                                                                                       |
| ------------------------------------------ | ----------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------- |
| `health-check.md` (workflow-runner-style)  | Hermes cron + a markdown skill                  | 🟡 Redesign     | Same spirit, native scheduling.                                                             |
| Machine setup (mac/linux)                  | Stays useful, just orient toward `hermes setup` | 🟢 Port         | One-line installer + this doc is a good combo.                                              |
| Machine security review                    | Same; lightly Hermes-aware                      | 🟢 Port         |                                                                                             |
| Tailscale docs                             | Same                                            | 🟢 Port         |                                                                                             |
| Notification routing                       | Hermes gateway handles routing natively         | 🟡 Redesign     | Doc shrinks; the routing logic is built in.                                                 |
| Embeddings setup                           | Replace with `memory-deep-dive.md`              | 🟡 Redesign     | Different storage layer entirely.                                                           |
| Cron fleet manifest                        | Hermes cron + small fleet plugin                | 🟡 Redesign     |                                                                                             |
| App router (Caddyfile, auth-service, etc.) | —                                               | ⚪ Out of scope | Tailscale-served services; not agent infrastructure. Fork from `openclaw-config` if needed. |
| Remote desktop setup                       | Same                                            | 🟢 Port         | Unchanged by Hermes.                                                                        |
| `apt-packages.txt`, `Brewfile`             | Same; add Hermes deps                           | 🟢 Port         |                                                                                             |

## API keys & secrets

| OpenClaw                        | Hermes                                     | Action      | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------- | ------------------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `~/.openclaw/.env`              | `~/.hermes/.env`                           | 🟢 Port     | `claw migrate --migrate-secrets` ports a documented base allowlist (`TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `VOICE_TOOLS_OPENAI_KEY`) **plus per-provider env vars for every custom provider it finds** (e.g. `LMSTUDIO_API_KEY`, `GOOGLE_API_KEY`, plus provider-specific keys named after each custom provider in your `openclaw.json`). Verified against a real migration: roughly 10+ env vars moved in addition to the base 6. Always dry-run first to see the exact list for your config. See `migrator-internals.md`. |
| Per-instance API key separation | Hermes profiles can use distinct env files | 🟡 Redesign | If you ran one OpenClaw per persona, you'll likely collapse to one Hermes with multiple profiles.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |

## Per-instance migration checklist

When converting an OpenClaw instance to Hermes, walk this list (top to bottom):

1. **Inventory** — what's running on the OpenClaw instance? (Use the gap-analysis tool
   when it ships.)
2. **Dry-run migration** — `hermes claw migrate --dry-run`. Read every line of the
   output.
3. **Choose Telegram strategy** — same bot or new bot? (See `telegram-and-reactions.md`
   _(in flight)_.)
4. **Run migration** — `hermes claw migrate` (or `--preset user-data` to skip secrets).
5. **Trim** — delete imported OpenClaw skills you don't need (they live in
   `~/.hermes/skills/openclaw-imports/`).
6. **Configure Hermes additions**:
   - Memory provider (see `memory-deep-dive.md` _(in flight)_)
   - Gateway (`hermes gateway setup`)
   - Cron jobs (port the OpenClaw crontab entries to Hermes format — `claw migrate`
     archives them as JSON for reference)
   - Profiles if multi-user
   - Sandboxes if any agent will execute arbitrary code
7. **Burn-in for two weeks** — let the self-improvement loop produce skills.
8. **Final prune** — remove any imported skills that the agent hasn't touched and didn't
   replace with a better one.
9. **Decommission OpenClaw** when comfortable. Keep the workspace tarballed for 90 days.

## Open questions feeding back into this doc

- What's the right memory provider per use case? See `memory-providers.md` _(in
  flight)_.
- How exactly does the Telegram bot handoff work? See `telegram-and-reactions.md` _(in
  flight)_.
- What does `hermes claw migrate` move and not move? See `migrator-internals.md` _(in
  flight)_.

This doc will be updated as the research lands. Watch for follow-up PRs.
