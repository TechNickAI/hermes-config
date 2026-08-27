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

# ------------------------------------------------------------ gateway code skew
# A gateway process serves the code it booted with. Pulling or checking out new code
# does not restart anything, so a long-lived gateway can drift many commits behind the
# checkout it runs from while every other check here still reports healthy. The failure
# only surfaces later, as an ImportError in a cron job, which is exactly the "nothing
# errors, the gap surfaces later" class this script exists to catch.
#
# The reference point is the last time HEAD actually moved (the reflog), not the HEAD
# commit date. Checking out an older branch or tag rewrites the working tree while the
# commit date goes backwards, and comparing against the commit date would miss that.
# The reflog is only written when HEAD really moves, so a no-op pull cannot trigger this.
checkout="$HERMES_HOME/hermes-agent"
if [ -d "$checkout/.git" ] && command -v git >/dev/null 2>&1 && command -v pgrep >/dev/null 2>&1; then
  # Extract the epoch from inside the reflog selector's braces rather than stripping
  # non-digits from the whole token: the selector carries a ref name, and a ref with
  # digits in it would otherwise concatenate into a bogus far-future timestamp.
  disk_epoch=$(git -C "$checkout" log -g -1 --date=unix --format=%gd HEAD 2>/dev/null \
    | sed -n 's/.*@{\([0-9][0-9]*\)}.*/\1/p')
  if [ -z "$disk_epoch" ]; then
    # No reflog (shallow clone, or the reflog expired). Fall back to the commit date.
    disk_epoch=$(git -C "$checkout" log -1 --format=%ct 2>/dev/null)
    case "$disk_epoch" in "" | *[!0-9]*) disk_epoch="" ;; esac
  fi

  # Pick the date dialect once, explicitly. BSD/macOS parses with -j -f, GNU with -d,
  # and the two flags mean different things on the other platform, so probing beats
  # letting one form fall through to the other on failure.
  date_dialect=""
  if date -j -f '%Y' '2000' +%s >/dev/null 2>&1; then
    date_dialect="bsd"
  elif date -d '2000-01-01' +%s >/dev/null 2>&1; then
    date_dialect="gnu"
  fi

  if [ -n "$disk_epoch" ] && [ -n "$date_dialect" ]; then
    # pgrep -f matches on the whole argv, so this script's own process tree matches
    # whenever it is launched from a shell whose command line happens to contain the
    # pattern (a CI step, an operator's one-liner). Walk our own ancestry once and skip
    # those pids, otherwise the verifier reports itself as a stale gateway.
    own_pids=" $$ "
    walk=$$
    # Capped and monotonic: ps can report a ppid that does not decrease (pid reuse,
    # reparenting), and a smoke test that hangs is worse than one that misses a warning.
    hops=0
    while [ "$walk" -gt 1 ] 2>/dev/null && [ "$hops" -lt 32 ]; do
      parent=$(ps -o ppid= -p "$walk" 2>/dev/null | tr -dc '0-9')
      [ -n "$parent" ] || break
      [ "$parent" -lt "$walk" ] 2>/dev/null || break
      own_pids="$own_pids$parent "
      walk="$parent"
      hops=$((hops + 1))
    done

    skewed=0
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      case "$own_pids" in *" $pid "*) continue ;; esac
      # LC_ALL=C so the weekday and month abbreviations match the parse format below
      # regardless of the operator's locale. ps pads lstart, so squeeze before parsing.
      started=$(LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null | tr -s ' ' | sed 's/^ *//; s/ *$//')
      [ -n "$started" ] || continue
      if [ "$date_dialect" = "bsd" ]; then
        boot_epoch=$(LC_ALL=C date -j -f '%a %b %d %H:%M:%S %Y' "$started" +%s 2>/dev/null)
      else
        boot_epoch=$(LC_ALL=C date -d "$started" +%s 2>/dev/null)
      fi
      # A skew warning is a nicety and must never break the script, so anything that
      # does not parse to a plain integer is skipped rather than guessed at.
      boot_epoch=$(printf '%s' "$boot_epoch" | tr -d '[:space:]')
      case "$boot_epoch" in "" | *[!0-9]*) continue ;; esac

      if [ "$boot_epoch" -lt "$disk_epoch" ]; then
        skewed=$((skewed + 1))
        # Approximate, and only ever shown as approximate: --since counts by committer
        # date, which rebases and cherry-picks reorder.
        behind=$(git -C "$checkout" rev-list --count HEAD --since="@$boot_epoch" 2>/dev/null \
          | tr -dc '0-9')
        if [ -n "$behind" ] && [ "$behind" -gt 0 ]; then
          warn "gateway pid $pid booted before the current checkout (~$behind commits behind)"
        else
          warn "gateway pid $pid booted before the checkout last changed, so it runs stale code"
        fi
      fi
    done < <(pgrep -f 'hermes.*gateway run' 2>/dev/null)

    if [ "$skewed" -gt 0 ]; then
      note "restart to pick up the code on disk: hermes gateway restart"
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
