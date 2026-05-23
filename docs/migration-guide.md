# OpenClaw → Hermes Migration — A Real-World Runbook

This is the actual sequence used to migrate **Hex** (a Linode Ubuntu utility instance,
~/.openclaw → Hermes) from OpenClaw to Hermes, including the bugs encountered and
how to work around them. Companion to [`knowledge/migrator-internals.md`](../knowledge/migrator-internals.md),
which documents the migrator code itself.

## TL;DR — the happy path

```bash
# 1. Install Hermes
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# 2. Dry-run first, always
hermes claw migrate --preset full --overwrite --migrate-secrets --dry-run

# 3. Stop OpenClaw before live migration (avoids Telegram bot-token races)
systemctl --user stop openclaw-gateway && systemctl --user disable openclaw-gateway

# 4. Live migration
hermes claw migrate --preset full --overwrite --migrate-secrets --yes

# 5. Fix the migrator's model-default bug (see "Known migrator bugs" below)
hermes config set model.default anthropic/claude-sonnet-4.6   # strip openrouter/ prefix

# 6. Install + start Hermes gateway
hermes gateway install   # answer y to start + y to enable on boot

# 7. Verify
hermes config check
hermes cron status
hermes cron run <job_id>   # force-tick a cron and inspect output
```

If you have custom OpenClaw workflows or cron jobs, those need manual porting — see
"Workflows → Skills" below.

## Phase 0 — Pre-flight snapshots

Before touching anything, pull these to local disk for forensic reference:

```bash
mkdir -p ~/migration-artifacts/openclaw-host && cd ~/migration-artifacts/openclaw-host

# Identity / persona / memory files (custom workspace)
scp host:~/openclaw/workspace/{SOUL,MEMORY,USER}.md ./
scp -r host:~/openclaw/workspace/workflows ./

# OpenClaw config + sensitive metadata (DO NOT commit)
scp host:~/.openclaw/openclaw.json ./openclaw.json.snapshot
ssh host 'openclaw cron list --json' > cron-jobs-raw.json

# Systemd unit + currently-set env
scp host:~/.config/systemd/user/openclaw-gateway.service ./
```

Why: the migrator's `archive_dir` only archives files under `~/.openclaw/` — anything
under a custom workspace path like `~/openclaw/workspace/` won't be backed up
automatically. You want a known-good copy on a separate machine before you change
state.

## Phase 1 — Install Hermes

```bash
ssh host 'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash'
ssh host 'export PATH=$HOME/.local/bin:$PATH && hermes --version'
```

The installer creates a stock `~/.hermes/` with a default `SOUL.md`, `MEMORY.md`, etc.
The migration will overwrite (or merge with) these in Phase 3.

## Phase 2 — Dry-run

Always dry-run first:

```bash
hermes claw migrate --preset full --overwrite --migrate-secrets --dry-run
```

Read the summary line carefully:

```
Summary: N migrated, M conflict(s), K skipped
```

- **migrated** = items the live run would write
- **conflict** = items that exist on both sides; `--overwrite` would replace them (with
  `~/.hermes/migration/openclaw/<ts>/backups/` for each)
- **skipped** = `--skill-conflict skip` items, sensitive files (creds, sqlite), and
  things with no matching destination
- **error** = anything > 0 here means STOP and debug before live

If the dry-run looks reasonable, proceed.

## Phase 3 — Live migration

```bash
hermes claw migrate --preset full --overwrite --migrate-secrets --yes
```

Two things you should verify in the output:

1. **`✓ Migration complete!`** at the end (no traceback)
2. Conflict and skip counts roughly match the dry-run

If it crashes mid-flight (see "Known migrator bugs"), the partial state may already be
on disk. Check the report directory: `~/.hermes/migration/openclaw/<timestamp>/`.

## Phase 4 — Allowlist & cleanup

The migrator handles most env vars, but verify a few things by hand:

```bash
# Telegram allowlist
grep TELEGRAM_ALLOWED_USERS ~/.hermes/.env

# Custom providers (e.g. 9router)
grep -A3 "custom_providers:" ~/.hermes/config.yaml

# Drop any deprecated env vars (e.g. MESSAGING_CWD → terminal.cwd in config.yaml)
sed -i '/^MESSAGING_CWD=/d' ~/.hermes/.env
```

## Phase 5 — Workflows → Skills

The migrator does **not** port OpenClaw workflows (`workspace/workflows/<name>/`).
You have to do this by hand. The Hermes-native mapping:

| OpenClaw concept              | Hermes equivalent                         |
| ----------------------------- | ----------------------------------------- |
| `workflows/<name>/AGENT.md`   | `~/.hermes/skills/<name>/SKILL.md`        |
| `workflows/<name>/config.md`  | merged into the skill's front-matter table |
| OpenClaw cron job ID          | `hermes cron create ... --skill <name>`   |
| Workflow `delivery: none`     | `hermes cron create ... --deliver local`  |
| Per-workflow state files      | `~/.hermes/cron/output/<job_id>/` or skill-managed |

This repo carries two reference conversions you can copy as templates:

- [`skills/cron-healthcheck/SKILL.md`](../skills/cron-healthcheck/SKILL.md)
- [`skills/pr-review-sweep/SKILL.md`](../skills/pr-review-sweep/SKILL.md)

After installing the skill under `~/.hermes/skills/<name>/SKILL.md`, create the cron:

```bash
hermes cron create "5 * * * *" "Run the <name> skill. <task-specific instructions>" \
  --name <name> --skill <name> --deliver local
```

## Phase 6 — Gateway cutover

```bash
# Stop OpenClaw FIRST (avoids duplicate bot polling)
systemctl --user stop openclaw-gateway
systemctl --user disable openclaw-gateway

# Install + start Hermes gateway
hermes gateway install            # interactive: y to start, y to enable on boot
systemctl --user status hermes-gateway

# Confirm Telegram is live (no errors in this grep = healthy)
journalctl --user -u hermes-gateway --since "1 minute ago" | grep -iE "error|telegram"
```

Hermes only logs failures for Telegram; silence = connected.

## Phase 7 — Verification

End-to-end smoke test:

```bash
# 1. Config sanity
hermes config check                 # all required envs set
hermes config show | grep -A2 model # model resolves cleanly

# 2. Force a cron tick
hermes cron run <healthcheck_job_id>
sleep 60  # wait for scheduler tick + agent run
ls -lt ~/.hermes/cron/output/<healthcheck_job_id>/ | head -3
cat ~/.hermes/cron/output/<healthcheck_job_id>/<latest>.md | tail -10

# Look for "HEARTBEAT_OK" or successful completion (NOT "FAILED")
```

If a cron run shows `FAILED` with a model error, see "Known migrator bugs" below.

## Phase 8 — Documentation

Update your fleet registry / inventory to reflect the new runtime
(`OpenClaw v…` → `Hermes v…`), and commit your migration artifacts to a private
repo if you keep one.

## Phase 9 — Decommission OpenClaw

Once Hermes has run cleanly for at least one full cron cycle:

```bash
# Tarball the old install for safekeeping
tar czf ~/openclaw-pre-hermes-$(date +%Y%m%d).tgz ~/.openclaw

# Remove the systemd unit (already stopped + disabled in Phase 6)
rm ~/.config/systemd/user/openclaw-gateway.service
systemctl --user daemon-reload

# (Optional) remove openclaw binaries
pip3 uninstall openclaw    # or wherever it was installed from
```

Keep the tarball around until you're confident the migration is durable — at minimum
through one full cycle of every cron job.

## Known migrator bugs

These were hit during real-world migrations and not yet fixed upstream. Each has a
matching footnote in `knowledge/migrator-internals.md`.

### 1. `archive_path` same-file crash (when source lives outside `~/.openclaw/`)

**Symptom:** Migration crashes with `SameFileError` during the archive_docs step when
your `workspace` directory is a custom path like `~/openclaw/workspace/` rather than
the default `~/.openclaw/workspace/`.

**Root cause:** In `openclaw_to_hermes.py` `archive_path()`, the call
`self.archive_dir / relative_label(source, self.source_root)` produces an
**absolute** path on the right side when source lives outside source_root. Python's
`Path / abs_path` collapses to `abs_path` — so destination == source and
`shutil.copy2` errors out.

**Patch** (apply locally to the installed migrator until upstream fixes it):

```python
def archive_path(self, source: Path, reason: str) -> None:
    rel = relative_label(source, self.source_root)
    if isinstance(rel, str) and rel.startswith("/"):
        rel = source.name  # fall back to basename when source is outside source_root
    destination = self.archive_dir / rel if self.archive_dir else None
    if self.execute and destination is not None:
        if source.resolve() == destination.resolve():
            self.record("archive", source, destination, "skipped",
                        f"{reason} (source and destination resolved to same file — skipped)")
            return
        ensure_parent(destination)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
        self.record("archive", source, destination, "archived", reason)
    else:
        self.record("archive", source, destination, "archived", reason)
```

File:
`~/.hermes/hermes-agent/optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py`

### 2. Model default prefix mismatch

**Symptom:** First cron run (or first agent invocation) fails with
`openrouter/anthropic/claude-sonnet-4.6 is not a valid model ID` from OpenRouter.

**Root cause:** The migrator copies the OpenClaw model identifier verbatim
(`openrouter/anthropic/claude-sonnet-4.6`) but the resulting config also sets
`base_url: https://openrouter.ai/api/v1`. When the base_url already points at
OpenRouter, the model ID must NOT carry the `openrouter/` prefix — OpenRouter expects
just `anthropic/claude-sonnet-4.6`.

**Fix:** After migration, run:

```bash
hermes config set model.default anthropic/claude-sonnet-4.6
systemctl --user restart hermes-gateway
```

Same applies to any other provider where the OpenClaw model ID was namespaced with
the routing layer's prefix.

## Pitfalls

- **Don't run the migration with OpenClaw still polling Telegram.** Bot tokens can't
  be in two long-polling clients at once — you'll get one polling client returning
  409 Conflict and a stuck gateway. Stop OpenClaw first.
- **Don't trust `is-active` exit codes.** `systemctl is-active` returns 3 for
  inactive/stopped which `set -e` will trip over — wrap in `|| true` if you're
  scripting this.
- **Skill name collisions.** The migrator's default `--skill-conflict skip` means if
  the install pre-seeded a skill with the same name as one you're porting, your
  custom one is silently skipped. Either pre-clean `~/.hermes/skills/<name>/` before
  migrating or use `--skill-conflict overwrite`.
- **The `migrated` count is misleading after a partial-failure retry.** If the first
  live run crashed partway through, the second `--overwrite` run will report many
  items as "conflict" rather than "migrated" — but they ARE actually correct (the
  first run wrote them, the second checked and found them already present). Trust
  the on-disk state, not the count.
