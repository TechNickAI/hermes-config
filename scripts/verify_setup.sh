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

  # Dependency checks for skills that cannot work without external tooling. A skill
  # installed without its dependency is the silent failure this script exists to catch.
  check_dep() {  # check_dep <skill> <test-command> <fix-hint>
    if [ -d "$HERMES_HOME/skills/$1" ]; then
      if eval "$2" >/dev/null 2>&1; then
        ok "$1 dependency satisfied"
      else
        bad "$1 is installed but its dependency is missing"
        note "$3"
      fi
    fi
  }
  check_dep google-docs        "command -v gog" "install the gog CLI, then: gog auth login"
  check_dep google-sheets      "command -v gog" "install the gog CLI, then: gog auth login"
  check_dep google-slides      "command -v gog" "install the gog CLI, then: gog auth login"
  check_dep address-pr-comments "command -v gh" "install the gh CLI, then: gh auth login"
  check_dep pr-review-sweep    "command -v gh"  "install the gh CLI, then: gh auth login"
  check_dep grok-search        '[ -n "${XAI_API_KEY:-}" ] || grep -q XAI_API_KEY "'"$HERMES_HOME"'/.env" 2>/dev/null' \
    "set XAI_API_KEY in $HERMES_HOME/.env (get one at https://console.x.ai)"
else
  warn "no skills directory — nothing installed yet"
  note "mkdir -p $HERMES_HOME/skills && cp -r skills/recall $HERMES_HOME/skills/"
fi

# ----------------------------------------------------------------- memory plugin
if [ -d "$HERMES_HOME/plugins/cortex" ] || [ -d "$HERMES_HOME/plugins/memory/cortex" ]; then
  ok "cortex plugin present"
  # Copying the plugin without pointing config at it is a no-op that looks like success.
  if [ -f "$HERMES_HOME/config.yaml" ] && grep -qE '^\s*provider:\s*cortex' "$HERMES_HOME/config.yaml"; then
    ok "config.yaml selects the cortex memory provider"
  else
    bad "cortex is copied but config.yaml does not select it — the plugin is inert"
    note "set  memory.provider: cortex  in $HERMES_HOME/config.yaml"
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
