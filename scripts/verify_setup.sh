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

# ------------------------------------------------ observability plugin egress
# An enabled agent-observability plugin is the inverse of the cortex case above:
# not a copy that silently does nothing, but a config line that silently does a
# lot. The agento11y plugin ships whole conversations (prompts, responses, the
# system prompt, tool arguments and tool results) to a Grafana Cloud stack, with
# no PII redaction, unless AGENTO11Y_CONTENT_CAPTURE_MODE=metadata_only is set.
# It is also invisible to `hermes plugins list`, which does not see pip-installed
# plugins, so config.yaml is the only place this shows up locally.
if [ -f "$HERMES_HOME/config.yaml" ]; then
  o11y_enabled=""
  o11y_parsed=""
  o11y_degraded=""
  # Parse the YAML when possible: a bare grep for the plugin name also matches a
  # commented-out line or a disabled list, and warning about a plugin that is not
  # actually on is how a check like this trains people to ignore it.
  if command -v python3 >/dev/null 2>&1; then
    o11y_result=$(python3 - "$HERMES_HOME/config.yaml" <<'PYEOF'
import sys

try:
    import yaml
except ImportError:
    print("unparsed")  # no PyYAML: let the caller fall back to grep
    sys.exit(0)
try:
    with open(sys.argv[1]) as handle:
        data = yaml.safe_load(handle) or {}
except Exception:
    print("unparsed")  # unreadable or invalid YAML: grep is better than nothing
    sys.exit(0)
plugins = data.get("plugins") if isinstance(data, dict) else None
enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
if isinstance(enabled, list) and any(str(n).strip() == "agento11y" for n in enabled):
    print("enabled")
else:
    print("absent")
PYEOF
)
    case "$o11y_result" in
      enabled) o11y_enabled="yes"; o11y_parsed="yes" ;;
      absent)  o11y_parsed="yes" ;;
    esac
  fi
  # Grep fallback whenever the structured parse did not happen (no python3, no
  # PyYAML, unreadable file). A plain grep cannot tell an enabled entry from a
  # disabled one, a comment, or an unrelated list, and it misses flow style
  # (enabled: [agento11y]). So it does not pretend to decide: if the name appears
  # at all, report that verification was degraded and let the reader check. A
  # missing dependency must not silently turn a content-egress check into no
  # check, and it must not claim a certainty this path does not have.
  if [ -z "$o11y_parsed" ] \
    && grep -qE "(^|[^[:alnum:]_-])agento11y([^[:alnum:]_-]|$)" "$HERMES_HOME/config.yaml"; then
    o11y_degraded="yes"
  fi

  if [ -n "$o11y_enabled" ]; then
    # Resolve the mode the way Hermes will at runtime. Hermes loads
    # $HERMES_HOME/.env with override on, so a value there beats an exported
    # shell variable with no warning. Checking only the shell would report safe
    # on a box that is about to export everything.
    capture_mode="${AGENTO11Y_CONTENT_CAPTURE_MODE:-}"
    if [ -f "$HERMES_HOME/.env" ]; then
      # python-dotenv tolerates spaces around the '=', so this must too, or a
      # correctly-configured box gets warned at.
      env_line=$(grep -E '^[[:space:]]*(export[[:space:]]+)?AGENTO11Y_CONTENT_CAPTURE_MODE[[:space:]]*=' \
        "$HERMES_HOME/.env" 2>/dev/null | tail -1)
      if [ -n "$env_line" ]; then
        env_mode="${env_line#*=}"
        env_mode="${env_mode#"${env_mode%%[![:space:]]*}"}"
        env_mode="${env_mode%%[[:space:]]*}"
        env_mode="${env_mode%\"}"; env_mode="${env_mode#\"}"
        env_mode="${env_mode%\'}"; env_mode="${env_mode#\'}"
        capture_mode="$env_mode"
      fi
    fi

    if [ "$capture_mode" = "metadata_only" ]; then
      ok "agento11y enabled in metadata_only mode (no conversation content leaves the machine)"
    else
      warn "agento11y is enabled and exporting full conversation content off this machine"
      note "that includes prompts, responses, the system prompt, and tool args/results, unredacted"
      note "to keep content local: set AGENTO11Y_CONTENT_CAPTURE_MODE=metadata_only"
    fi
  elif [ -n "$o11y_degraded" ]; then
    warn "config.yaml mentions agento11y but this check could not read it properly"
    note "install PyYAML (python3 -m pip install pyyaml) to verify the plugin's capture mode"
    note "if the plugin is enabled, full conversation content leaves the machine unless"
    note "AGENTO11Y_CONTENT_CAPTURE_MODE=metadata_only is set"
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
