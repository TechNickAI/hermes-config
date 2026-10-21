#!/usr/bin/env bash
# Install weekly session-store maintenance on every profile of ONE host.
#
# Run this ON each host (or via ssh <host> 'bash -s' < this file).
# Idempotent: re-running replaces the job rather than duplicating it.
#
# What it does per profile:
#   1. copies dbmaint.py + weekly_db_maintenance.py into that profile's scripts/
#   2. registers a `db-maintenance` cron job for Sunday 04:00 local
#
# It does NOT prune anything. The catch-up is a separate, supervised step --
# see catchup_all.sh. This only puts the recurring job in place.
#
# Profiles are staggered 20 minutes apart so two on a shared host never
# contend for the same disk.

set -euo pipefail

SRC_DIR="${1:-/tmp/dbmaint-deploy}"
DRY="${DRY_RUN:-0}"

PY="$HOME/.hermes/hermes-agent/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

for f in dbmaint.py weekly_db_maintenance.py; do
  [ -f "$SRC_DIR/$f" ] || { echo "missing $SRC_DIR/$f"; exit 2; }
done

slot=0
for base in "$HOME/.hermes" "$HOME"/.hermes/profiles/*; do
  [ -d "$base" ] || continue
  case "$base" in *profiles) continue;; esac
  [ -f "$base/state.db" ] || continue

  name=$(basename "$base")
  [ "$base" = "$HOME/.hermes" ] && name="_root"

  # Stagger: 04:00, 04:20, 04:40, ...
  minute=$(( (slot * 20) % 60 ))
  hour=$(( 4 + (slot * 20) / 60 ))
  slot=$(( slot + 1 ))

  echo "=== $name -> Sunday ${hour}:$(printf %02d $minute) ==="
  if [ "$DRY" = "1" ]; then continue; fi

  mkdir -p "$base/scripts"
  cp "$SRC_DIR/dbmaint.py" "$base/scripts/dbmaint.py"
  cp "$SRC_DIR/weekly_db_maintenance.py" "$base/scripts/weekly_db_maintenance.py"
  "$PY" -m py_compile "$base/scripts/dbmaint.py" \
                      "$base/scripts/weekly_db_maintenance.py"

  MIN="$minute" HOUR="$hour" BASE="$base" NAME="$name" "$PY" - <<'PYEOF'
import json, os, time, uuid, shutil

base = os.environ["BASE"]
name = os.environ["NAME"]
expr = f"{os.environ['MIN']} {os.environ['HOUR']} * * 0"

path = os.path.join(base, "cron", "jobs.json")
os.makedirs(os.path.dirname(path), exist_ok=True)
if os.path.exists(path):
    shutil.copy(path, path + ".bak-dbmaint-" + time.strftime("%Y%m%d%H%M%S"))
    data = json.load(open(path))
else:
    data = {"jobs": []}

jobs = data.setdefault("jobs", [])

# Clone the key set of an existing no_agent job so `cron list` renders the
# next-run time. A hand-written entry missing optional keys schedules fine but
# displays "Next run: ?", which reads like a broken job.
template = next((j for j in jobs if j.get("no_agent")), None)
job = {k: None for k in template} if template else {}

job.update({
    "id": uuid.uuid4().hex[:12],
    "name": "db-maintenance",
    "schedule": {"kind": "cron", "expr": expr, "display": expr},
    "schedule_display": expr,
    "script": "weekly_db_maintenance.py",
    "no_agent": True,
    "deliver": "telegram:-1003922432033:25539",
    "repeat": {"times": None, "completed": 0},
    "enabled": True,
    "state": "scheduled",
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "prompt": "", "skills": [], "context_from": [], "workdir": None,
})

jobs[:] = [j for j in jobs if j.get("name") != "db-maintenance"]
jobs.append(job)
json.dump(data, open(path, "w"), indent=2)
print(f"  registered db-maintenance ({expr})")
PYEOF
done

echo
echo "Done. Verify with:  hermes [-p <profile>] cron list | grep -A4 db-maintenance"
