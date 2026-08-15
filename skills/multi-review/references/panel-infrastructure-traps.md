# Panel infrastructure traps

These are launch/orchestration failures, not reviewer judgments. Diagnose them before
calling a panel degraded. The command shapes below were exercised against a live profile
on macOS: binary provenance resolved through a running gateway, a configured alias
returned a one-shot response, a removed alias failed before inference, and the bounded
poller terminated on both success and timeout.

## 1. Wrong Hermes binary

**Observed symptom.** A panel runner explicitly selected an executable under
`~/.local/bin/hermes` before falling back to `command -v hermes`. The runner therefore
looked deliberate, yet inspection of the real gateway showed it was loading packages
from a different install tree. A shell-resolved `hermes --version` looked healthy and
still did not prove what the gateway was executing.

**Why it misleads.** Hermes may have moved between install methods while an old shim or
hard-coded path remains executable. A version string describes the process you just
launched, not the gateway's loaded Python packages.

First inspect the shell, then use the gateway process as the source of truth:

```bash
which hermes
hermes --version

# Select exactly one gateway for the profile being reviewed.
PROFILE=<profile>
PID=$(ps ax -o pid=,command= | perl -sne '
  print "$1\n" if /^\s*(\d+)\s+\S+\/python -m hermes_cli\.main --profile \Q$profile\E gateway run --replace\s*$/;
' -- -profile="$PROFILE" | head -n 1)

# This is the decisive provenance check.
lsof -p "$PID" | grep '/site-packages/' | head

PYTHON=$(ps -p "$PID" -o command= |
  perl -ne 'print "$1\n" if /^(\S+\/python)\s+-m\s+hermes_cli\.main/')
VENV=${PYTHON%/bin/python}
HERMES_BIN="$VENV/bin/hermes"
test -x "$HERMES_BIN"
lsof -p "$PID" | grep -q "$VENV/lib/.*site-packages"
"$HERMES_BIN" --version
```

If the gateway is the default profile, adjust only the exact `ps` matcher by removing
`--profile <profile>`; do not choose the first Hermes PID on a multi-profile host.

**Verification.** `lsof` must show `site-packages` beneath the same `VENV` used to build
`HERMES_BIN`, and that binary must be executable. Pass `"$HERMES_BIN"` explicitly to
every reviewer launch. Do not conclude that there is no discrepancy merely because
`which hermes` and `hermes --version` look plausible.

## 2. Stale custom-provider alias

**Observed symptom.** A removed alias failed immediately with non-zero exit and:

```text
hermes -z: agent failed: Unknown provider 'custom:<stale-alias>'. Check 'hermes model' for available providers, or run 'hermes doctor' to diagnose config issues.
```

An earlier panel hard-coded separate family aliases; provider renames/removals can make
that look like an empty reviewer, model failure, or timeout if stderr and exit status
are not inspected.

**Why it misleads.** The family can still be available as a model under another alias.
Provider aliases are profile-local routing keys, not stable family names.

Enumerate the live profile before assembling the panel:

```bash
PROFILE=<profile>
PROFILE_CONFIG="$HOME/.hermes/profiles/$PROFILE/config.yaml"
HERMES_PROFILE="$PROFILE" python3 - "$PROFILE_CONFIG" <<'PY'
from pathlib import Path
import sys
import yaml

providers = (yaml.safe_load(Path(sys.argv[1]).read_text()).get("providers") or {})
for alias, block in providers.items():
    print(f"custom:{alias}")
    for model in ((block or {}).get("models") or {}):
        print(f"  {model}")
PY
```

Choose a printed alias/model pair, then smoke-test the exact route before the full
review. Names below are placeholders, not defaults:

```bash
PROFILE=<profile>
PROVIDER=custom:<enumerated-alias>
MODEL=<model-listed-under-that-alias>
HERMES_PROFILE="$PROFILE" "$HERMES_BIN" -z \
  'Reply with exactly: PANEL_ALIAS_OK' \
  --provider "$PROVIDER" -m "$MODEL" --ignore-rules -t ''
```

**Verification.** Require exit `0` and non-empty stdout (for the smoke test, exactly
`PANEL_ALIAS_OK`). On non-zero exit, retain and read stderr; do not reclassify
`Unknown provider` as model slowness. Re-enumerate aliases for each panel run instead of
copying yesterday's provider name.

## 3. Poll loop that never breaks

**Observed symptom.** A background panel finished except for one reviewer, while the
watcher kept waiting on it. The recognizable broken shape is an unbounded liveness loop:

```bash
# BROKEN: no deadline, no iteration bound, no timeout exit.
while kill -0 "$pid" 2>/dev/null; do
  sleep 5
done
```

**Why it misleads.** The poller itself remains healthy, so the orchestrator appears busy
instead of failed. A reviewer that wedges, loses its provider, or never writes output
can hold synthesis forever.

Use all three bounds: a wall-clock deadline, a maximum poll count, and explicit breaks
for process completion and timeout. This is the tested single-reviewer shell shape; an
orchestrator should give each reviewer its own state/output file and apply the same
bound independently.

```bash
PROFILE=<profile>
PROVIDER=custom:<enumerated-alias>
MODEL=<model-listed-under-that-alias>
OUT=$(mktemp "${TMPDIR:-/tmp}/panel-reviewer.XXXXXX")
MAX_POLLS=60
POLL_SECONDS=5
DEADLINE_SECONDS=300
start=$SECONDS
deadline=$((start + DEADLINE_SECONDS))

HERMES_PROFILE="$PROFILE" "$HERMES_BIN" -z "$PROMPT" \
  --provider "$PROVIDER" -m "$MODEL" --ignore-rules -t '' >"$OUT" 2>&1 &
pid=$!

status=running
rc=0
for ((poll=1; poll<=MAX_POLLS; poll++)); do
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"
    rc=$?
    if ((rc == 0)) && [[ -s "$OUT" ]]; then
      status=success
    else
      status=failed
    fi
    break  # explicit success/failure exit
  fi

  if ((SECONDS >= deadline || poll == MAX_POLLS)); then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    status=timeout
    rc=124
    break  # explicit timeout exit
  fi

  remaining=$((deadline - SECONDS))
  sleep_for=$POLL_SECONDS
  ((sleep_for > remaining)) && sleep_for=$remaining
  ((sleep_for < 1)) && sleep_for=1
  sleep "$sleep_for"
done

printf 'status=%s polls=%d elapsed=%ds bytes=%s\n' \
  "$status" "$poll" "$((SECONDS-start))" "$(wc -c <"$OUT" | tr -d ' ')"
((rc == 0)) || { sed -n '1,80p' "$OUT" >&2; exit "$rc"; }
```

The loop uses Bash's `SECONDS`; run it with Bash. A tested success ended as
`status=success ...` with the reviewer's expected response. A forced two-poll run ended
as `status=timeout polls=2 ...` with exit `124`, proving the iteration cap terminates
the watcher even before its later deadline.

**Verification.** Test both branches before using the panel: a tiny real reviewer must
finish with `status=success`; a deliberately tiny `MAX_POLLS` must finish with
`status=timeout` and exit `124`. For normal/deep review budgets, keep the 300/600-second
policy and degradation rules in
[slow-reviewer-timeouts-router-path.md](slow-reviewer-timeouts-router-path.md); do not
replace a slow routed reviewer with an unapproved direct endpoint.
