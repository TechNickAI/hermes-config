#!/usr/bin/env bash
# Nightly Cortex health check + repair for one profile.
#
# Verifies the derived Cortex database (SQLite, FTS5, embedding coverage) and
# proves real semantic retrieval still works. Repairs are backup-first and touch
# only derived state; markdown pages are never modified.
#
# Output contract: SILENT when healthy and nothing needed doing. Speaks only
# when it repaired something or when the store is still broken.
#
# Exit: 0 healthy, 1 unhealthy, 2 setup failure.
set -uo pipefail

# CORTEX_STORE is an OPTIONAL override. When unset the doctor resolves the store
# and database from plugins.cortex in the profile's config.yaml, which is what the
# live agent uses. Passing a store unconditionally would defeat that.
STORE="${CORTEX_STORE:-}"
PROFILE_HOME="${CORTEX_PROFILE_HOME:-$HOME/.hermes}"
QUERY="${CORTEX_DOCTOR_QUERY:-memory}"
# Default to the directory this script lives in. The doctor and summary scripts
# are siblings, so self-location works both from a repo checkout and from an
# installed plugin dir -- guessing a path under PROFILE_HOME does not.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="${CORTEX_PLUGIN_DIR:-$(dirname -- "$SCRIPT_DIR")}"
KEEP_DAYS="${CORTEX_KEEP_BACKUP_DAYS:-14}"
PYTHON="${CORTEX_PYTHON:-}"

if [[ -z "$PYTHON" ]]; then
  for candidate in \
    "$HOME/.hermes/hermes-agent/venv/bin/python" \
    "$PROFILE_HOME/hermes-agent/venv/bin/python" \
    "$(command -v python3 || true)"; do
    if [[ -x "$candidate" ]]; then PYTHON="$candidate"; break; fi
  done
fi

DOCTOR="$PLUGIN_DIR/scripts/nightly_doctor.py"
if [[ ! -x "$PYTHON" || ! -f "$DOCTOR" ]]; then
  echo "🔴 Cortex doctor SETUP FAILURE on $(hostname -s): python='$PYTHON' doctor='$DOCTOR'"
  exit 2
fi

ARGS=(--profile-home "$PROFILE_HOME" --query "$QUERY" --keep-backup-days "$KEEP_DAYS" --repair)
if [[ -n "$STORE" ]]; then
  ARGS+=(--store "$STORE")
fi

OUT="$("$PYTHON" "$DOCTOR" "${ARGS[@]}" 2>&1)"
RC=$?

case "$RC" in
  0)
    # Healthy. Stay silent unless the doctor actually had to intervene.
    if echo "$OUT" | grep -q '"repairs": \[\]'; then
      exit 0
    fi
    echo "🔧 Cortex self-repaired on $(hostname -s)"
    printf '%s' "$OUT" | "$PYTHON" "$PLUGIN_DIR/scripts/nightly_doctor_summary.py" || printf '%s\n' "$OUT"
    exit 0
    ;;
  2)
    echo "🔴 Cortex doctor could not run on $(hostname -s) — invocation/precondition failure, store untouched"
    echo "$OUT"
    exit 2
    ;;
  *)
    echo "🔴 Cortex UNHEALTHY on $(hostname -s) — repair did not restore service"
    echo "$OUT"
    exit "$RC"
    ;;
esac
