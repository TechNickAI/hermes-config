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

STORE="${CORTEX_STORE:-$HOME/.hermes/cortex}"
PROFILE_HOME="${CORTEX_PROFILE_HOME:-$HOME/.hermes}"
QUERY="${CORTEX_DOCTOR_QUERY:-memory}"
PLUGIN_DIR="${CORTEX_PLUGIN_DIR:-$PROFILE_HOME/plugins/cortex}"
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

OUT="$("$PYTHON" "$DOCTOR" --store "$STORE" --profile-home "$PROFILE_HOME" \
        --query "$QUERY" --keep-backup-days "$KEEP_DAYS" --repair 2>&1)"
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
