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

# ---------------------------------------------------------------------- profiles
# Every check above reads exactly one Hermes home. A machine running profiles has
# several, and a profile is where the quiet copy failures actually accumulate. Without
# this block the script inspects one home out of N and prints "healthy", which is the
# same false-green it exists to prevent.
#
# Warnings only, never failures. A profile with no skills is a normal profile, and a
# checker that fails on a healthy fleet gets ignored, which costs more than it catches.
# The script does not re-invoke itself per profile either; it reports what it did not
# check and the exact command to check it.
profiles_dir="$HERMES_HOME/profiles"
# Suppress when this run already targets a profile home, so a per-profile invocation
# does not nag about its siblings or about itself.
if [ "$(basename "$(dirname "$HERMES_HOME")")" != "profiles" ] && [ -d "$profiles_dir" ]; then
  profile_count=$(find "$profiles_dir" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
  if [ "$profile_count" -gt 0 ]; then
    warn "$profile_count profile(s) found; every check above covered only $HERMES_HOME"
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      # Quote the path so the printed command is copy-pastable even for a profile
      # directory containing spaces or shell metacharacters.
      note "not inspected: $(basename "$p")   re-run with: HERMES_HOME=$(printf '%q' "$p") scripts/verify_setup.sh"

      # Two shapes are cheap and unambiguous without a full re-run, and both are
      # already checked on the root home above.
      #
      # First: a skill directory that resolves to no skill at all, the shape an
      # interrupted `cp -r` leaves behind. The test is "no SKILL.md anywhere beneath
      # it", not "no SKILL.md directly inside it". Hermes resolves skills by walking
      # the tree, so category directories holding nested skills are normal and a
      # depth-1 test reports dozens of healthy categories as broken. Dot directories
      # are Hermes metadata, not skills, and are skipped for the same reason.
      if [ -d "$p/skills" ]; then
        while IFS= read -r sd; do
          [ -n "$sd" ] || continue
          case "$(basename "$sd")" in
            .*) continue ;;
          esac
          # Prune dot and cache directories during the walk, not just at the top
          # level. Hermes ignores them, so an archived .archive/SKILL.md must not
          # make an otherwise empty directory look whole.
          found=$(find "$sd" \
            \( -name '.*' -o -name '__pycache__' -o -name 'node_modules' \) -prune \
            -o -name SKILL.md -type f -print -quit 2>/dev/null)
          if [ -z "$found" ]; then
            if [ -f "$sd/DESCRIPTION.md" ]; then
              warn "$(basename "$p")/skills/$(basename "$sd") is an empty category, DESCRIPTION.md but no skills inside"
            else
              warn "$(basename "$p")/skills/$(basename "$sd") has no SKILL.md anywhere, incomplete copy"
            fi
          fi
        done < <(find "$p/skills" -maxdepth 1 -mindepth 1 -type d)
      fi

      # Second: a cortex plugin copied in but not selected by that profile's own
      # config.yaml, which is inert and looks like success.
      if [ -d "$p/plugins/cortex" ] || [ -d "$p/plugins/memory/cortex" ]; then
        if [ -f "$p/config.yaml" ] \
          && grep -qE "^[[:space:]]*provider:[[:space:]]*[\"']?cortex[\"']?[[:space:]]*$" "$p/config.yaml"; then
          : # selected, nothing to report
        else
          warn "$(basename "$p") carries cortex but its config.yaml does not select it, so it is inert"
        fi
      fi
    done < <(find "$profiles_dir" -maxdepth 1 -mindepth 1 -type d | sort)
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
