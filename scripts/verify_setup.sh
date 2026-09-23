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
  # A skill directory with no SKILL.md is a broken copy — the most common failure
  # mode of a partial or interrupted `cp -r`.
  #
  # But not every top-level directory is a skill. Hermes also supports CATEGORY
  # directories that hold nested skills plus a DESCRIPTION.md, and it resolves
  # skills by walking the tree (agent/skill_utils.py iter_skill_index_files uses
  # os.walk, with no depth-1 assumption). Treating a category as a broken skill
  # made this script report dozens of false failures on a healthy install, which
  # is the exact inversion of what a verifier is for: a real failure becomes one
  # line among many, and "incomplete copy" invites deleting a correct tree.
  #
  # Dot-directories are Hermes metadata, not skills. Hermes prunes them itself
  # (EXCLUDED_SKILL_DIRS covers .hub, .archive, .git and friends), so flagging
  # .hub or .curator_backups as broken skills is noise about internal state the
  # user never copied.
  #
  # Nesting is not limited to one level: categories can hold sub-categories
  # (mlops/inference/llama-cpp/SKILL.md is a real three-deep layout), so the
  # search is unbounded in depth. It prunes the same support directories Hermes
  # prunes — references/templates/assets/scripts can hold archived SKILL.md
  # files that are progressive-disclosure data, not skill roots — plus dot and
  # cache directories, so the count matches what Hermes will actually load.
  #
  # -mindepth 1, not 2: with -mindepth 2 the prune clause is never evaluated
  # against a support directory sitting directly under the category, so an
  # archived references/SKILL.md would still be counted and could make an
  # incomplete copy look whole. Only reached when the directory has no SKILL.md
  # of its own, so nothing is double counted.
  count_nested_skills() {
    find "$1" -mindepth 1 -type d \
      \( -name '.*' -o -name '__pycache__' -o -name 'node_modules' \
         -o -name 'references' -o -name 'templates' -o -name 'assets' -o -name 'scripts' \) -prune \
      -o -name SKILL.md -type f -print 2>/dev/null | wc -l | tr -d ' '
  }

  skill_count=0
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    name=$(basename "$d")
    case "$name" in
      .*) continue ;;
    esac

    if [ -f "$d/SKILL.md" ]; then
      skill_count=$((skill_count + 1))
      continue
    fi

    nested=$(count_nested_skills "$d")
    if [ "${nested:-0}" -gt 0 ]; then
      # A category. Its children are the skills.
      skill_count=$((skill_count + nested))
      if [ ! -f "$d/DESCRIPTION.md" ]; then
        warn "$name is a category with $nested skill(s) but no DESCRIPTION.md"
        note "add $d/DESCRIPTION.md so the category is described to the agent"
      fi
    elif [ -f "$d/DESCRIPTION.md" ]; then
      # Described as a category, but nothing is behind it. It resolves to no
      # skills at all, which is the same user-visible outcome as an incomplete
      # copy, so it keeps the original contract and fails. The message says what
      # is actually wrong rather than pointing at a missing SKILL.md that was
      # never supposed to be there.
      bad "$name is an empty category — DESCRIPTION.md but no skills inside"
      note "copy a skill into $d/ or remove the directory"
    else
      bad "$name has no SKILL.md — incomplete copy, Hermes will ignore it"
    fi
  done < <(find "$HERMES_HOME/skills" -maxdepth 1 -mindepth 1 -type d)

  ok "skills directory present ($skill_count installed)"

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
  #
  # Config is not only the root file. Each profile under profiles/<name>/config.yaml
  # carries its own memory.provider, and a fleet can deliberately leave the root
  # unset while every profile selects cortex. Checking the root alone reported a
  # working memory backend as inert and invited a config edit that was not needed.
  cortex_provider_re="^[[:space:]]*provider:[[:space:]]*[\"']?cortex[\"']?[[:space:]]*$"
  cortex_selected_by=""

  if [ -f "$HERMES_HOME/config.yaml" ] && grep -qE "$cortex_provider_re" "$HERMES_HOME/config.yaml"; then
    cortex_selected_by="config.yaml"
  fi

  cortex_profiles=""
  if [ -d "$HERMES_HOME/profiles" ]; then
    while IFS= read -r profile_config; do
      [ -n "$profile_config" ] || continue
      if grep -qE "$cortex_provider_re" "$profile_config"; then
        profile_name=$(basename "$(dirname "$profile_config")")
        cortex_profiles="${cortex_profiles:+$cortex_profiles, }$profile_name"
      fi
    done < <(find "$HERMES_HOME/profiles" -maxdepth 2 -mindepth 2 -name config.yaml -type f 2>/dev/null | sort)
  fi

  if [ -n "$cortex_selected_by" ] && [ -n "$cortex_profiles" ]; then
    ok "cortex selected in config.yaml and by profile(s): $cortex_profiles"
  elif [ -n "$cortex_selected_by" ]; then
    ok "config.yaml selects the cortex memory provider"
  elif [ -n "$cortex_profiles" ]; then
    ok "cortex selected by profile(s): $cortex_profiles"
    note "the root config.yaml does not select it, which is fine if that is deliberate"
  else
    bad "cortex is copied but no config selects it — the plugin is inert"
    note "set  memory.provider: cortex  in $HERMES_HOME/config.yaml"
    note "or in a profile at $HERMES_HOME/profiles/<name>/config.yaml"
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
