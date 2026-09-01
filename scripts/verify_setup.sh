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

# -------------------------------------------------------------- pause reach (ESTOP)
# `hermes pause` writes an ESTOP sentinel under the HERMES_HOME of the process that
# runs it, and every component checks the sentinel under its own HERMES_HOME. Installed
# gateway services each carry their own HERMES_HOME in their service definition, so on a
# multi-home box a pause engaged here can report success while gateways homed elsewhere
# keep running. Nothing on the machine says so, which is the same quiet-gap failure this
# script exists to catch.
#
# This reports where gateways are homed. It never writes or removes a sentinel, never
# touches a service definition, and never increments fails: per-home pausing is a
# legitimate choice, and the defect is that the reach of the control is invisible, not
# that it is wrong.
gateway_homes=""
gateway_count=0

# macOS launchd. Read the string that follows the HERMES_HOME key in the environment
# block. A definition we cannot parse contributes nothing rather than a guess.
if [ -d "$HOME/Library/LaunchAgents" ]; then
  while IFS= read -r plist; do
    [ -n "$plist" ] || continue
    home=$(awk '
      /<key>HERMES_HOME<\/key>/ { want = 1; next }
      want && /<string>/ {
        line = $0
        sub(/.*<string>/, "", line)
        sub(/<\/string>.*/, "", line)
        print line
        exit
      }
    ' "$plist" 2>/dev/null)
    [ -n "$home" ] || continue
    gateway_count=$((gateway_count + 1))
    gateway_homes="$gateway_homes$home"$'\n'
  done < <(find "$HOME/Library/LaunchAgents" -maxdepth 1 -name '*hermes*gateway*.plist' 2>/dev/null)
fi

# Linux systemd user units. Environment=HERMES_HOME=... with optional quoting.
for unit_dir in "$HOME/.config/systemd/user" "/etc/systemd/user"; do
  [ -d "$unit_dir" ] || continue
  while IFS= read -r unit; do
    [ -n "$unit" ] || continue
    home=$(sed -n 's/^[[:space:]]*Environment=["]\{0,1\}HERMES_HOME=\([^"]*\)["]\{0,1\}[[:space:]]*$/\1/p' "$unit" 2>/dev/null | head -1)
    [ -n "$home" ] || continue
    gateway_count=$((gateway_count + 1))
    gateway_homes="$gateway_homes$home"$'\n'
  done < <(find "$unit_dir" -maxdepth 1 -name '*hermes*gateway*.service' 2>/dev/null)
done

# No installed gateway services is the normal single-machine case. Say nothing.
if [ "$gateway_count" -gt 0 ]; then
  # Trailing slashes would produce a false mismatch against an otherwise equal path.
  # Compare as fixed whole lines (-Fx): a home like ~/.hermes contains a regex dot, and
  # a pattern match there would silently swallow a genuinely different home.
  checked_home="${HERMES_HOME%/}"
  other_homes=$(printf '%s' "$gateway_homes" | sed 's:/*$::' | grep -Fxv "$checked_home" | sort -u)

  if [ -z "$other_homes" ]; then
    ok "pause reach: all $gateway_count installed gateway service(s) homed at the checked home"
  else
    other_count=$(printf '%s\n' "$other_homes" | grep -c .)
    warn "pause reach: $other_count gateway home(s) differ from the checked home"
    note "a pause engaged at $checked_home does not bind processes homed elsewhere,"
    note "unless the installed Hermes carries the fleet-root ESTOP fallback"
    note "(NousResearch/hermes-agent commit ad08a58)"
    while IFS= read -r other; do
      [ -n "$other" ] || continue
      note "  gateway home: $other"
    done <<< "$other_homes"
    note "pause each home directly, or upgrade Hermes, if you meant to stop everything"
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
