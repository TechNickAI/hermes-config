# `hermes claw migrate` — internals

## Recommendation up front

For a typical OpenClaw user with custom skills, workflows, and cron jobs:

```bash
hermes claw migrate --preset full --overwrite --migrate-secrets
```

This single command moves everything the built-in migrator knows how to move, including
the allowlisted API keys, overwriting any prior Hermes state (with backups). It does
**not** migrate workflows (no option for them) and it does **not** recreate cron jobs —
it only archives them as JSON for you to recreate by hand via `hermes cron`. You will
need a per-user supplementor for those two categories.

## Where the code lives

| File                                                                                                | LOC   |
| --------------------------------------------------------------------------------------------------- | ----- |
| `~/.hermes/hermes-agent/hermes_cli/claw.py`                                                         | ~810  |
| `~/.hermes/hermes-agent/optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py` | ~3136 |

The CLI in `claw.py` is the user-facing entry point; it dispatches to the script under
the optional skill, which holds all migration logic.

## The 28 migration options

All options are declared as keys in `MIGRATION_OPTION_METADATA` (`openclaw_to_hermes.py`
L45-L186). Each entry has a `label` and `description`. Below is the full list grouped by
behavior, with the source under `~/.openclaw/` and destination under `~/.hermes/`.

### Imported into Hermes (live data)

| Option id                | Source                                                                                                                               | Destination                                                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `soul`                   | `~/.openclaw/workspace/SOUL.md`                                                                                                      | `~/.hermes/` (persona file)                                                                                         |
| `workspace-agents`       | `~/.openclaw/workspace/AGENTS.md`                                                                                                    | `<--workspace-target>/AGENTS.md`                                                                                    |
| `memory`                 | `~/.openclaw/workspace/MEMORY.md` (falls back to `workspace.default/MEMORY.md`)                                                      | `~/.hermes/memories/MEMORY.md`                                                                                      |
| `user-profile`           | `~/.openclaw/workspace/USER.md` (with same fallback)                                                                                 | `~/.hermes/memories/USER.md`                                                                                        |
| `daily-memory`           | `~/.openclaw/workspace/memory/*.md`                                                                                                  | merged into `~/.hermes/memories/MEMORY.md`                                                                          |
| `messaging-settings`     | `~/.openclaw/openclaw.json`                                                                                                          | `~/.hermes/.env` (allowlists, working dir)                                                                          |
| `discord-settings`       | `~/.openclaw/openclaw.json`                                                                                                          | `~/.hermes/.env`                                                                                                    |
| `slack-settings`         | `~/.openclaw/openclaw.json`                                                                                                          | `~/.hermes/.env`                                                                                                    |
| `whatsapp-settings`      | `~/.openclaw/openclaw.json`                                                                                                          | `~/.hermes/.env`                                                                                                    |
| `signal-settings`        | `~/.openclaw/openclaw.json`                                                                                                          | `~/.hermes/.env` (account, HTTP URL, allowlist)                                                                     |
| `command-allowlist`      | `~/.openclaw/openclaw.json` (exec approval patterns)                                                                                 | `~/.hermes/config.yaml` `command_allowlist` (merged)                                                                |
| `skills` ("user skills") | `~/.openclaw/workspace/skills/<skill>/` (each must contain `SKILL.md`)                                                               | `~/.hermes/skills/openclaw-imports/<skill>/`                                                                        |
| `shared-skills`          | `~/.openclaw/skills/`, `~/.agents/skills/`, `~/.openclaw/workspace/.agents/skills/`, `~/.openclaw/workspace.default/.agents/skills/` | `~/.hermes/skills/openclaw-imports/`                                                                                |
| `tts-assets`             | `~/.openclaw/workspace/` TTS audio files                                                                                             | corresponding Hermes location                                                                                       |
| `model-config`           | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` (default model)                                                                             |
| `tts-config`             | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` (TTS provider + voice)                                                                      |
| `mcp-servers`            | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` (MCP server definitions)                                                                    |
| `agent-config`           | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` agent defaults (compaction, context, thinking); multi-agent list archived                   |
| `gateway-config`         | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` (port + auth); rest archived                                                                |
| `session-config`         | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` `session_reset` (daily/idle policies)                                                       |
| `full-providers`         | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` `custom_providers` (baseUrl, apiType, headers)                                              |
| `deep-channels`          | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` extended channel settings (Matrix, Mattermost, IRC, group configs); complex pieces archived |
| `browser-config`         | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml`                                                                                             |
| `tools-config`           | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` (exec timeout, sandbox, web search)                                                         |
| `approvals-config`       | `openclaw.json`                                                                                                                      | `~/.hermes/config.yaml` `approvals` (mode + rules)                                                                  |
| `archive`                | unmapped-but-compatible docs                                                                                                         | archive dir under `~/.hermes/`                                                                                      |

### Secrets (gated behind `--migrate-secrets`)

| Option id         | Source                                                                         | Destination                            |
| ----------------- | ------------------------------------------------------------------------------ | -------------------------------------- |
| `secret-settings` | `openclaw.json` allowlist + `~/.openclaw/agents/main/agent/auth-profiles.json` | `~/.hermes/.env`                       |
| `provider-keys`   | `openclaw.json`                                                                | `~/.hermes/.env` (model provider keys) |

### Archive-only (NOT imported — preserved as JSON for manual review)

| Option id        | What it does                                                                                                                                                               |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cron-jobs`      | Writes `openclaw.json`'s `cron.*` to the archive dir as `cron-config.json`; also copies `~/.openclaw/cron/` to `archive/cron-store/`. Migrator does **not** recreate jobs. |
| `plugins-config` | Plugin configuration + installed extensions archived                                                                                                                       |
| `hooks-config`   | Internal hooks, webhooks, Gmail integration archived                                                                                                                       |
| `memory-backend` | QMD, vector search, citations settings archived                                                                                                                            |
| `skills-config`  | Per-skill enabled/config/env from `skills.entries` archived                                                                                                                |
| `ui-identity`    | UI theme, assistant identity, display preferences archived                                                                                                                 |
| `logging-config` | Logging and diagnostics archived                                                                                                                                           |

## The presets

Defined in `MIGRATION_PRESETS` (`openclaw_to_hermes.py` L187-L224). **Only two presets
exist in source:** `user-data` and `full`. There is no `"default"` literal — the CLI's
argparse default is `"full"` (`claw.py` L329:
`preset = getattr(args, "preset", "full")`).

`MIGRATION_PRESETS["user-data"]` (L188-L222) selects: `soul`, `workspace-agents`,
`memory`, `user-profile`, `messaging-settings`, `command-allowlist`, `skills`,
`tts-assets`, `discord-settings`, `slack-settings`, `whatsapp-settings`,
`signal-settings`, `model-config`, `tts-config`, `shared-skills`, `daily-memory`,
`archive`, `mcp-servers`, `agent-config`, `session-config`, `browser-config`,
`tools-config`, `approvals-config`, `deep-channels`, `full-providers`, `plugins-config`,
`cron-jobs`, `hooks-config`, `memory-backend`, `skills-config`, `ui-identity`,
`logging-config`, `gateway-config`.

In practice `user-data` already covers everything **except** the two secret-bearing
options (`secret-settings`, `provider-keys`). The difference from `full` is just those
two.

`MIGRATION_PRESETS["full"]` (L223) is literally `set(MIGRATION_OPTION_METADATA)` — every
key. Even so, secrets are still gated behind the separate `--migrate-secrets` flag (see
"Secrets handling" below).

## Conflict modes

There are two distinct conflict systems.

### File conflicts (everything except skills)

Controlled by `--overwrite`. From `_print_migration_report` in `claw.py` L743:

```
⚠ Conflicts (skipped — use --overwrite to force)
```

Default: skip on conflict. With `--overwrite`: existing file gets backed up (unless
`--no-backup`) and replaced. There is also an apply- ordering safeguard: once any
`config.yaml`-mutating option hits a conflict or error, the remaining config-mutating
options are short- circuited rather than risk a partial YAML write. The full set of
config-mutating options is declared as `_CONFIG_MUTATING_OPTIONS` in
`openclaw_to_hermes.py` and includes 19 options (`model-config`, `tts-config`,
`mcp-servers`, `plugins-config`, `cron-jobs`, `hooks-config`, `agent-config`,
`gateway-config`, `session-config`, `full-providers`, `deep-channels`, `browser-config`,
`tools-config`, `approvals-config`, `memory-backend`, `skills-config`, `ui-identity`,
`logging-config`, `command-allowlist`). Dry-run mode never short-circuits.

### Skill directory conflicts

A separate `--skill-conflict` flag with three values
(`SKILL_CONFLICT_MODES = {"skip", "overwrite", "rename"}`, L35).

- `skip` (default): record the existing skill dir as a `conflict`, leave it alone.
- `overwrite`: replace the existing skill directory.
- `rename`: copy the imported skill under `<name>-imported` (or `<name>-imported-2`,
  `-3`, ... if even the renamed target exists). See `resolve_skill_destination` in the
  migrator.

Skills always land under `~/.hermes/skills/openclaw-imports/<skill-name>/`
(`SKILL_CATEGORY_DIRNAME = "openclaw-imports"`, L31). A `DESCRIPTION.md` is written in
that category dir on first run.

## Secrets handling

There are two paths through which secrets end up in `~/.hermes/.env`:

**Path 1 — the base allowlist.** `SUPPORTED_SECRET_TARGETS` (`openclaw_to_hermes.py`
L36-L43) is exactly six env vars:

```
TELEGRAM_BOT_TOKEN
OPENROUTER_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
ELEVENLABS_API_KEY
VOICE_TOOLS_OPENAI_KEY
```

These are migrated by the `secret-settings` option, sourced from `openclaw.json` and
`~/.openclaw/agents/main/agent/auth-profiles.json`.

**Path 2 — per-provider keys (the surprise).** The `provider-keys` and `full-providers`
options _also_ write env vars to `.env` — one per custom provider configured in the
source `openclaw.json`. Verified against a real migration: in addition to the base 6,
roughly ten more env vars landed — a mix of always-on (`HERMES_GATEWAY_TOKEN`), public
provider keys (`LMSTUDIO_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`), and one or two
keys named after each custom provider configured in the source (e.g.
`MY_ROUTER_API_KEY`, `MY_ROUTER_ANTHROPIC_API_KEY`).

The "exactly six" framing in the comment header is only true for the `secret-settings`
option in isolation. Under `--preset full --migrate-secrets`, expect the union of the
base 6 plus one key per custom provider.

Even with `--preset full`, secrets are **never** included unless `--migrate-secrets` is
also passed. From `claw.py` L336-L340:

> Secrets are never included implicitly — they must be explicitly requested via
> `--migrate-secrets`, even under `--preset full`. This mirrors OpenClaw's
> migrate-hermes posture (two-phase: run once without secrets, rerun with
> `--include-secrets`) and prevents a `--preset full` invocation from silently importing
> API keys that the user may not have intended to copy.

## Custom providers migrate in a shape Hermes' model resolver doesn't read

This is a gotcha to know about. The `full-providers` option writes custom providers into
the new `custom_providers:` field as a list:

```yaml
custom_providers:
  - name: my-router
    base_url: http://127.0.0.1:<port>/v1
    api_key: ""
    api_mode: chat_completions
```

But Hermes' model resolver reads from the older `providers:` dict format and uses
`key_env:` (pointing at an env var) rather than `api_key:` (which the migrator writes as
the empty string). A freshly-migrated profile typically also has:

```yaml
model:
  default: <provider-name>/<model-id> # single slash-string
providers: {} # ← empty, the resolver finds nothing here
```

In a real migration, this caused gateway requests to silently fall through to a
different provider (whichever is listed as fallback) and return HTTP 400 _"… is not a
valid model ID"_ because the slash-string was sent to a provider that doesn't know that
ID.

**Workaround until the migrator is fixed:** after migration, mirror the working
`providers:` dict shape from a known-good profile (e.g. `~/.hermes/config.yaml` on a
machine where the same custom provider already works). Specifically:

1. Replace the `model:` block with the working profile's (`default: chat`, plus
   `provider:`, `base_url:`, `api_mode:` siblings).
2. Replace the empty `providers: {}` with the working profile's full provider dict (each
   entry needs `name:`, `base_url:`, `key_env:`, `api_mode:`, and a `models:` block
   listing model IDs).
3. Drop the unused `custom_providers:` list.
4. Make sure the env var named by `key_env:` actually exists in `.env` (the
   migrator-written keys may be named differently — alias as needed).

This is worth flagging upstream — the migration writes provider config that doesn't work
end-to-end on Hermes ≥ v0.14.

## Cron handling — archive only, no recreation

`migrate_cron_jobs` (`openclaw_to_hermes.py` L2217+) does two things:

1. If `openclaw.json` has a `cron` section, writes it to `<archive>/cron-config.json`
   (L2228-L2230).
2. If `~/.openclaw/cron/` exists, copies the whole tree to `<archive>/cron-store/`
   (L2239-L2243).

No Hermes cron jobs are created. The recorded `reason` field is:

> Cron config archived. Use 'hermes cron' to recreate jobs manually.

The post-migration notes (`_build_next_steps`, L2915-L2918) say:

```
- Run `hermes cron` to recreate scheduled tasks (see archive/cron-config.json)
- Run `hermes cron` to recreate scheduled tasks (see archived cron-store)
```

And the printed report (`claw.py` L3118-L3119, in the migrator):

```
3. Recreate cron jobs: hermes cron
```

The archive dir lives under `~/.hermes/` (chosen by the migrator's
`--output-dir`/default report root). **Implication:** if you have many cron jobs, plan
on rebuilding each one via `hermes cron` after migration.

## Workflow handling — not migrated at all

```
grep -in workflow openclaw_to_hermes.py  # → 0 matches
grep -in workflow claw.py                # → 0 matches
```

There is no migration option for workflows. Nothing in `MIGRATION_OPTION_METADATA`
references workflows. If you ran OpenClaw workflows (anything from
`~/.openclaw/workspace/workflows/`), the migrator will not detect, archive, or recreate
them. They survive in the source `~/.openclaw/` tree but never reach `~/.hermes/`.

**Implication:** workflow recreation is entirely a user-supplied task. A custom
supplementor that walks `~/.openclaw/workspace/workflows/` and translates each one into
the Hermes equivalent is the right pattern.

## Skills handling

The `skills` option (called "User skills" in metadata) reads from
`~/.openclaw/workspace/skills/` (with `workspace.default/skills/` as a fallback via
`source_candidate`), iterates each subdirectory that contains a `SKILL.md`, and copies
it into `~/.hermes/skills/openclaw-imports/<skill-name>/` using the `--skill-conflict`
mode described above.

The separate `shared-skills` option checks four other source roots
(`openclaw_to_hermes.py` L1824-L1829):

```
~/.openclaw/skills/
~/.agents/skills/
~/.openclaw/workspace/.agents/skills/
~/.openclaw/workspace.default/.agents/skills/
```

Anything found there is also copied under `openclaw-imports/`.

## `--dry-run` output

The CLI calls `_print_migration_report(preview_report, dry_run=True)` (`claw.py` L703+).
The structure printed:

1. Header: `Dry Run Results` with "No files were modified."
2. Grouped item lists by status:
   - `Would migrate:` `<kind> → ~/.hermes/<destination-with-home-tilde>`
   - `Conflicts (skipped — use --overwrite to force):` `<kind>  <reason>`
   - `Skipped:` `<kind>  <reason>`
   - `Errors:` `<kind>  <reason>`
3. Summary line: `N would migrate, M conflict(s), P skipped, Q error(s)`
4. `Full report saved to: <output_dir>` (JSON report + `summary.md`)
5. Footer:
   `To execute the migration, run without --dry-run: hermes claw migrate --preset <name>`

**What to read carefully in a dry run:**

- **Conflicts** block — these will be skipped unless you add `--overwrite`. Anything you
  actually want to replace must show up here as expected.
- **Skipped** block — for each option you expected to migrate, confirm the reason isn't
  "Not selected for this run" (preset problem) or "No <source> found" (source path
  problem).
- The `--output-dir` JSON report contains the full structured plan, the `warnings` array
  (`_build_warnings`), and the `next_steps` array (`_build_next_steps`) — these surface
  cron, workflow gaps, and similar manual follow-ups.

## `--workspace-target`

`claw.py` L332, L393-L394, L422, L430, L532. Optional; when supplied,
`migrate_workspace_agents` writes the OpenClaw workspace `AGENTS.md`
(`WORKSPACE_INSTRUCTIONS_FILENAME = "AGENTS" + ".md"`, L44) to
`<workspace-target>/AGENTS.md`. If no `--workspace-target` is given, the
`workspace-agents` option records as skipped with `"No workspace target was provided"`.

This is for users who keep a project-level workspace alongside Hermes (distinct from
`~/.hermes/`) and want their persona/agent instructions copied into that project. The
text is rebranded via `rebrand_text` on copy (OpenClaw → Hermes naming).

## What is NOT migrated

Direct from source:

- **Workflows** — zero references in either file.
- **Cron job execution** — only archived; the user must rebuild.
- **Anything not in `MIGRATION_OPTION_METADATA`** — there is no catch-all walker. The
  `archive` option captures "compatible-but- unmapped docs" but only for manual review,
  not import.
- **Secrets beyond the six allowlisted env vars** — even with `--migrate-secrets`, any
  other env value in `openclaw.json` or `auth-profiles.json` is dropped on the floor.
- **Multi-agent list** — `agent-config` only imports agent defaults (compaction,
  context, thinking); the multi-agent list is archived, not imported.
- **Gateway full config** — `gateway-config` imports port + auth only; the rest is
  archived.
- **Deep channel complex settings** — extended group/Matrix/IRC configs are archived
  rather than imported.

## Migration strategy

### Bulk-migrate, then supplement (most users)

If you have any custom workflows, more than a couple of cron jobs, or heavily customized
plugins/hooks, expect a two-phase migration:

1. **Bulk phase** — the built-in migrator. From an OpenClaw home:

   ```bash
   hermes claw migrate --dry-run --preset full --migrate-secrets
   ```

   Read the dry-run output carefully (especially the `Conflicts` and `Skipped` blocks).
   Then:

   ```bash
   hermes claw migrate --preset full --overwrite --migrate-secrets
   ```

   `--overwrite` replaces existing Hermes files (with backups unless `--no-backup`). Add
   `--skill-conflict rename` if you have skills in both OpenClaw and Hermes you want to
   keep side by side.

2. **Supplementor phase** — a per-user script that handles what the built-in cannot:
   - Walk `~/.openclaw/workspace/workflows/` and recreate each workflow in the Hermes
     equivalent.
   - Read the archived `~/.hermes/<archive>/cron-config.json` and drive `hermes cron` to
     recreate each job.
   - Optionally restore selected pieces of the archived `plugins-config`,
     `hooks-config`, `gateway-config`, `deep-channels`, multi-agent `agent-config`,
     `memory-backend`, `skills-config`, `ui-identity`, and `logging-config` archives
     where they apply.

### Built-in is enough by itself

If your OpenClaw setup is light — no custom workflows, zero or one cron job you don't
mind rebuilding by hand, no heavy plugin/hook customization, just memories + a few
skills + messaging settings — then a single
`hermes claw migrate --preset full --overwrite --migrate-secrets` covers you. Walk the
dry-run output, walk the post-migration `next_steps` list, recreate the one cron job if
any, done.

### Custom migrator is warranted

If you have either of these, write a custom migrator (the built-in becomes one input
among several):

- **Heavy workflow inventory** — every workflow is a translation decision; a script that
  knows your workflow conventions is more reliable than manual recreation.
- **Many cron jobs with complex models/schedules** — driving `hermes cron` from a script
  that reads `cron-config.json` saves many manual invocations and centralizes the
  recipe.

In both cases, run the built-in first to handle the 80% of options that are mechanical
(memories, skills, messaging, secrets, MCP servers, model config, etc.), then layer the
custom pieces on top.

## Uncertainty / open questions

- The literal preset names are `user-data` and `full` only. The README language
  describing a "default" preset corresponds to the CLI's argparse default of `full`, not
  a third preset.
- Archive destination path: confirmed it's a directory under the Hermes target
  (referenced as `self.archive_dir`); the exact subpath is controlled by the migrator's
  report/output-dir machinery and was not exhaustively read here.
- The `tts-assets` source path was not explicitly enumerated beyond "compatible
  workspace audio files" in the metadata description; the precise filename rules live in
  the corresponding migrate method which was not read in full.
