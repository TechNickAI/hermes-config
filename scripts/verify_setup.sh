#!/usr/bin/env bash
# verify_setup.sh — prove a hermes-config setup actually took effect.
#
# The starter kit is a pile of copy commands, and copy commands fail quietly: a wrong
# path, a missing ~/.hermes/, a skill copied without its dependency. Nothing errors, the
# agent reports success, and the gap only surfaces later when a skill silently no-ops.
#
# This is the smoke test. It reads state and prints findings; it changes nothing.
# Exit 0 = everything checked is sound. Exit 1 = at least one real problem.

set -uo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
fails=0
warns=0

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fails=$((fails + 1)); }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; warns=$((warns + 1)); }
note() { printf '        %s\n' "$1"; }

echo "== hermes-config setup check =="
echo "   HERMES_HOME: $HERMES_HOME"
echo

# ---------------------------------------------------------------- Hermes itself
if command -v hermes >/dev/null 2>&1; then
  ok "hermes CLI on PATH ($(hermes --version 2>/dev/null | head -1 || echo 'version unknown'))"
else
  bad "hermes CLI not found — install Hermes first, this repo only configures it"
  note "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"
fi

if [ -d "$HERMES_HOME" ]; then
  ok "$HERMES_HOME exists"
else
  bad "$HERMES_HOME does not exist — run 'hermes setup' before copying anything in"
fi

# ---------------------------------------------------------------------- SOUL.md
if [ -f "$HERMES_HOME/SOUL.md" ]; then
  soul_lines=$(wc -l < "$HERMES_HOME/SOUL.md" | tr -d ' ')
  ok "SOUL.md present ($soul_lines lines)"
  # A SOUL.md that is byte-identical to a shipped preset means it was copied but never
  # personalised. That works, but it is not the point of the file.
  if [ -d "$(dirname "$0")/../templates/soul" ]; then
    for preset in "$(dirname "$0")/../templates/soul"/*.md; do
      [ -f "$preset" ] || continue
      if cmp -s "$preset" "$HERMES_HOME/SOUL.md"; then
        warn "SOUL.md is an unmodified copy of $(basename "$preset")"
        note "that works, but this file shapes every conversation — make it yours"
      fi
    done
  fi
else
  warn "no SOUL.md — the agent runs with default personality"
  note "cp templates/soul/<preset>.md $HERMES_HOME/SOUL.md"
fi

# ----------------------------------------------------------------------- skills
if [ -d "$HERMES_HOME/skills" ]; then
  count=$(find "$HERMES_HOME/skills" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
  ok "skills directory present ($count installed)"

  # A skill directory with no SKILL.md is a broken copy — the most common failure
  # mode of a partial or interrupted `cp -r`.
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    if [ ! -f "$d/SKILL.md" ]; then
      bad "$(basename "$d") has no SKILL.md — incomplete copy, Hermes will ignore it"
    fi
  done < <(find "$HERMES_HOME/skills" -maxdepth 1 -mindepth 1 -type d)

  # Dependency checks are driven by skills/MANIFEST.yaml rather than hardcoded here.
  # Hardcoding meant a skill could declare a dependency this script never checked —
  # google-slides declares pandoc, and the first version of this script silently
  # ignored it and reported healthy. Reading the manifest keeps one source of truth,
  # so declaring a new requirement automatically gets it verified.
  manifest="$(dirname "$0")/../skills/MANIFEST.yaml"
  if [ -f "$manifest" ] && command -v python3 >/dev/null 2>&1; then
    while IFS=$'\t' read -r skill requirement; do
      [ -n "$skill" ] || continue
      [ -d "$HERMES_HOME/skills/$skill" ] || continue

      # Map a declared requirement to a concrete probe. Unrecognised requirements are
      # reported as unverifiable rather than silently passed — claiming a dependency
      # is satisfied when it was never checked is the failure this script exists for.
      case "$requirement" in
        *"gog CLI"*)  probe="command -v gog"; hint="install the gog CLI, then: gog auth login" ;;
        *"gh CLI"*)   probe="command -v gh";  hint="install the gh CLI, then: gh auth login" ;;
        *pandoc*)     probe="command -v pandoc"; hint="install pandoc (brew install pandoc / apt install pandoc)" ;;
        "env: "*)
          var="${requirement#env: }"; var="${var%% *}"
          probe="[ -n \"\${$var:-}\" ] || grep -q '$var' '$HERMES_HOME/.env' 2>/dev/null"
          hint="set $var in the environment or $HERMES_HOME/.env" ;;
        *)
          warn "$skill requires '$requirement' — cannot verify automatically"
          note "confirm it yourself before relying on this skill"
          continue ;;
      esac

      if eval "$probe" >/dev/null 2>&1; then
        ok "$skill: $requirement"
      else
        bad "$skill is installed but requires: $requirement"
        note "$hint"
      fi
    done < <(python3 - "$manifest" <<'PYEOF'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)  # no PyYAML: skip dependency checks rather than fail the whole run
with open(sys.argv[1]) as handle:
    data = yaml.safe_load(handle) or {}
for skill in data.get("skills", []):
    for requirement in skill.get("requires") or []:
        print(f"{skill['name']}\t{requirement}")
PYEOF
)
  fi
else
  warn "no skills directory — nothing installed yet"
  note "mkdir -p $HERMES_HOME/skills && cp -r skills/recall $HERMES_HOME/skills/"
fi

# ----------------------------------------------------------------- memory plugin
if [ -d "$HERMES_HOME/plugins/cortex" ] || [ -d "$HERMES_HOME/plugins/memory/cortex" ]; then
  ok "cortex plugin present"
  # Copying the plugin without pointing config at it is a no-op that looks like success.
  # Accept quoted forms too: provider: cortex / "cortex" / 'cortex' are all valid YAML,
  # and failing a correctly-configured setup is worse than not checking at all.
  if [ -f "$HERMES_HOME/config.yaml" ] \
    && grep -qE "^[[:space:]]*provider:[[:space:]]*[\"']?cortex[\"']?[[:space:]]*$" "$HERMES_HOME/config.yaml"; then
    ok "config.yaml selects the cortex memory provider"
  else
    bad "cortex is copied but config.yaml does not select it — the plugin is inert"
    note "set  memory.provider: cortex  in $HERMES_HOME/config.yaml"
  fi
fi

# -------------------------------------------------------------------------- cron
# Built-in cron jobs are ticked by the gateway process, nothing else. Enabled jobs
# with no live gateway is the exact failure this script exists to catch: everything
# reads as configured, nothing ever fires, and the gap only surfaces when someone
# notices a report that never arrived. No cron configured is not a defect, so this
# section stays silent unless there is something to say.
if [ -f "$HERMES_HOME/cron/jobs.json" ] && command -v python3 >/dev/null 2>&1; then
  cron_enabled=$(python3 - "$HERMES_HOME/cron/jobs.json" <<'PYEOF'
import json
import sys

try:
    with open(sys.argv[1]) as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)  # unreadable or malformed: skip rather than fail the whole run

# Current stores are {"jobs": [...], "updated_at": ...}. Older ones were a bare list.
jobs = data.get("jobs", []) if isinstance(data, dict) else data
if not isinstance(jobs, list):
    sys.exit(1)

print(sum(1 for job in jobs if isinstance(job, dict) and job.get("enabled", True)))
print(len(jobs))
PYEOF
  )
  if [ -n "$cron_enabled" ]; then
    enabled_count=$(printf '%s\n' "$cron_enabled" | sed -n 1p)
    total_count=$(printf '%s\n' "$cron_enabled" | sed -n 2p)
    # Guard the arithmetic. This script must never abort the whole run because a
    # count came back unexpectedly empty; an unverifiable check is skipped, not fatal.
    case "$enabled_count" in
      "" | *[!0-9]*) enabled_count="" ;;
    esac
  else
    enabled_count=""
  fi
  if [ -n "$enabled_count" ]; then
    if [ "$enabled_count" -eq 0 ]; then
      ok "cron: $total_count job(s) configured, none enabled"
    else
      gateway_pid=""
      if [ -f "$HERMES_HOME/gateway.pid" ]; then
        gateway_pid=$(python3 - "$HERMES_HOME/gateway.pid" <<'PYEOF'
import json
import sys

try:
    raw = open(sys.argv[1]).read().strip()
except Exception:
    sys.exit(1)

try:
    pid = json.loads(raw).get("pid")  # current shape: {"pid": N, "kind": ...}
except Exception:
    pid = raw  # older shape: the bare pid

try:
    print(int(pid))
except Exception:
    sys.exit(1)
PYEOF
        )
      fi
      # kill -0 signals nothing; it only asks whether the process is still there.
      # A pid alone is not proof: pids are reused, so a dead gateway whose number
      # got recycled by an unrelated process would report healthy while nothing
      # fires. Confirm the process is actually a hermes gateway before saying ok.
      gateway_cmd=""
      if [ -n "$gateway_pid" ] && kill -0 "$gateway_pid" 2>/dev/null; then
        gateway_cmd=$(ps -p "$gateway_pid" -o command= 2>/dev/null)
      fi
      # Match a hermes token AND `gateway` as a standalone argument. The real
      # command line is ".../venv/bin/python -m hermes_cli.main gateway run",
      # so requiring the bare word excludes near misses that merely mention the
      # gateway in a path, such as `tail -f /var/log/hermes-gateway.log`.
      # Erring toward a false warning is safe here, a false ok is the whole bug.
      gateway_ok=no
      case "$gateway_cmd" in
        *hermes*)
          case "$gateway_cmd" in
            *" gateway "* | *" gateway") gateway_ok=yes ;;
          esac ;;
      esac
      if [ -z "$gateway_cmd" ]; then
        warn "cron: $enabled_count enabled job(s) but no live gateway, these jobs will not fire"
        note "start it with: hermes gateway start"
      elif [ "$gateway_ok" = yes ]; then
        ok "cron: $enabled_count enabled job(s), gateway pid $gateway_pid alive"
      else
        warn "cron: $enabled_count enabled job(s) but pid $gateway_pid is not a hermes gateway"
        note "stale $HERMES_HOME/gateway.pid, the pid was reused; run: hermes gateway start"
      fi
    fi
  fi
fi

# ------------------------------------------------------------------------ result
echo
if [ "$fails" -gt 0 ]; then
  echo "RESULT: $fails problem(s), $warns warning(s)"
  exit 1
fi
if [ "$warns" -gt 0 ]; then
  echo "RESULT: healthy, $warns warning(s)"
else
  echo "RESULT: healthy"
fi
exit 0
