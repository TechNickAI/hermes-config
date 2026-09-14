#!/bin/bash
# Parallel diverse-model reviewer panel for multi-review.
# Copy + edit the run() lines (name/provider/model/lens) per task.
#
# WHY THIS SHAPE:
#  - One shared brief file keeps every reviewer on identical facts (no anchoring,
#    no per-reviewer drift). Build the brief ONCE in the parent with real tools,
#    reduce to a bounded self-contained doc, then fan it out.
#  - Each reviewer writes its own .txt (stdout) and .err (stderr) so a single
#    hung/failed model can't corrupt the others and you can see partial failures.
#  - All reviewers run in parallel with `&` ... `wait`. 6 lenses finish in the
#    time of the slowest one, not the sum.
#  - Launch THIS script as a Hermes background process (terminal background=true,
#    notify_on_complete=true). Reviewer models are slow with long prompts;
#    blocking the parent turn on them is the classic mistake.
#
# EXECUTION RULES baked in (see SKILL.md "Running reviewers as Hermes one-shots"):
#  - `-t ''`        : disable tools so a headless reviewer can't hang on approval.
#  - `--ignore-rules`: strip the calling persona so the lens isn't washed out.
#                      RE-INJECT any privacy/PII constraints into the brief if the
#                      artifact may contain private data — --ignore-rules drops them.
#  - `timeout`      : 1800s. DO NOT LOWER THIS FOR ANALYTICAL PANELS.
#                      Root-caused 2026-08-15: `timeout 900` was KILLING working
#                      reviewers on a 15KB code-review brief. Measured, same
#                      model + same prompt: killed at 20s -> rc=124 with a
#                      0-byte .txt AND a 0-byte .err; given 900s -> 18,486 bytes,
#                      finishing at 389s. `hermes -z` buffers stdout and writes
#                      at the END, so a kill discards the whole review and leaves
#                      no diagnostic. A SLOW seat and a BROKEN seat look
#                      IDENTICAL. Two models were wrongly written off as dead
#                      this way. Always print rc per seat: rc=124 IS the
#                      diagnosis, and retrying a timeout with the SAME budget
#                      just reproduces it.
#  - isolation   : every reviewer runs in its own throwaway HERMES_HOME
#                  via scripts/reviewer_home.sh, so no reviewer can open
#                  the caller's live state.db. Never remove this. NEVER replace
#                  the helper with `cp -r ~/.hermes/profiles/<profile>` or
#                  `rsync`: four concurrent profile copies filled a 96 GB root
#                  filesystem and caused SQLite EIO on 2026-08-26. Seed only
#                  config.yaml/.env/auth.json; a seat creates its own database.
#  - `export HOME`  : set HOME explicitly. Headless `hermes -z` launched from a
#                      script/subprocess can fail with "Could not determine home
#                      directory" when HOME isn't inherited. Cheap, defensive.
#  - `export HERMES_HOME`: set the active profile explicitly before sourcing the
#                      helper. Profile credentials live there, not necessarily
#                      under $HOME/.hermes; the helper fails closed if neither
#                      HERMES_HOME nor REVIEWER_SOURCE_HOME is set.
#  - reviewer_run : use the helper API, never bare `hermes -z`. It owns seeding,
#                      provider pinning, and the required `-z "$prompt" [options]`
#                      order. Never execute reviewer_home.sh as a command.
#  - 2>"$OUT/$name.err": keep stderr OUT of the review text so model output stays clean.

export HOME=/home/ubuntu                 # <-- adjust to the real home if different
export HERMES_HOME=/home/ubuntu/.hermes/profiles/kenbot  # <-- active credential-bearing profile
cd /path/to/project || exit 1            # <-- repo root so reviewers see project context if needed
BRIEF="$(cat /tmp/review_brief.md)"      # <-- the bounded, self-contained artifact + context
OUT=/tmp/review_panel
mkdir -p "$OUT"

# Per-reviewer isolation helper (ships beside this template). Each run() sources
# it inside the seat process so concurrent calls never share REVIEWER_POOL state.
_MR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  local name="$1" provider="$2" model="$3" lens="$4"
  local prompt="${BRIEF}

================ YOUR ASSIGNED LENS: ${name} ================
${lens}

Respond in under 600 words. Be numeric and ruthless. For each finding give:
severity, evidence/location, why it matters in practice, smallest useful fix.
Distinguish real issues from tradeoffs and false positives."
  # ISOLATION IS MANDATORY. reviewer_run creates one seeded scratch home,
  # auto-pins the configured provider, strips shared board identity, and runs
  # the only supported argv shape: hermes -z "$prompt" [options]. If seeding
  # fails the seat fails closed; there is no unseeded fallback.
  timeout 1800 bash -c '
    source "$1"
    reviewer_pool_init || exit 1
    reviewer_run "$2" --provider "$3" -m "$4" --ignore-rules
  ' _ "${_MR_DIR}/scripts/reviewer_home.sh" "$prompt" "$provider" "$model" \
    > "$OUT/${name}.txt" 2>"$OUT/${name}.err"
  local rc=$?
  # rc IS the diagnosis. rc=124 means TIMEOUT KILLED A WORKING REVIEWER --
  # not a broken model, not a routing problem. Never write a seat off without it.
  local note=""
  [ "$rc" = 124 ] && note="  <-- TIMED OUT (raise the budget; do NOT retry at the same limit)"
  echo "[$name] rc=$rc bytes=$(wc -c <"$OUT/${name}.txt" 2>/dev/null)${note}"
}

# --- Edit these lines: one per lens. Use DIFFERENT model families for diversity. ---
# RESOLVE PROVIDER/MODEL FROM THE LIVE CONFIG, NEVER FROM MEMORY OR THIS TEMPLATE.
# Verified 2026-08-15 on trading.example.com: `custom:grok`, `custom:gemini` and
# `custom:openrouter` DO NOT EXIST on that profile -- each fails instantly with
# "Unknown provider" and a 0-byte .txt. That profile defines only `omniroute` and
# `openrouter-direct`, and routes model aliases server-side. Check first:
#   python3 -c "import yaml;c=yaml.safe_load(open('<profile>/config.yaml'));\
#   print(c['model']['provider']); print(list(c['providers']['omniroute']['models']))"
# The lines below use the omniroute shape; swap for whatever the local config wires up.
run "grok_contrarian" "custom:omniroute" "grok"              "RED-TEAM / challenge the premise. ..." &
run "gemini_coverage" "custom:omniroute" "gemini"            "LONG-CONTEXT coverage / evidence. ..." &
run "gpt_struct"      "custom:omniroute" "codex/gpt-5.6-sol" "STRUCTURED correctness / contracts. ..." &
run "kimi_depth"      "custom:omniroute" "kimi-k3"           "DEEP tradeoffs / dollars sizing. ..." &
run "think_reason"    "custom:omniroute" "think"             "REASONING-HEAVY angle / backtest design. ..." &

wait
echo "ALL REVIEWERS DONE"
# Then: read every $OUT/*.txt, synthesize, and run ONE meta-review one-shot against an
# independent model to catch double-counting / false positives before declaring a verdict.
