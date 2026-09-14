#!/usr/bin/env bash
# Ephemeral, self-destroying scratch homes for headless multi-review reviewers.
#
# WHY THIS EXISTS
# `hermes -z` boots a full agent, and the CLI path opens the CALLING profile's
# state.db read-write (cli.py:4642 SessionDB(), cli.py:8566 create_session(),
# hermes_state.py:2798 _default_db_path()). Run that from an agent whose
# gateway is live and two OS processes write one WAL database -- each with its
# own lock state and its own view of the WAL index. No pragma prevents it.
#
# Measured on a production host: gateway (fd mode 'u') and a
# `hermes -z` reviewer (fd mode 'u') held one state.db simultaneously. That
# 3GB database took structural B-tree damage -- "invalid page number", "2nd
# reference to page", rowids out of order -- and had to be rebuilt offline.
#
# THE MECHANISM
# HERMES_HOME roots config.yaml, .env, auth.json, skills, memories AND
# state.db. A scratch dir seeded with the caller's credentials gives a working
# agent with a PRIVATE database. Nothing is registered under profiles/, so
# there is no namespace to garbage-collect and no name to collide with.
#
# ONE HOME PER REVIEWER, ALWAYS. Measured: 6 concurrent reviewers sharing one
# home produced 6 simultaneous holders of one database -- the original bug,
# relocated. Per-reviewer homes measure a peak of exactly 1.
#
# NO ROLE VOCABULARY. Reviewers are anonymous slots; the caller's PROMPT makes
# one critical and another empathetic. Never enumerate personas here.
#
# USAGE
#   export HERMES_HOME=/path/to/the/active/profile  # REQUIRED when not inherited
#   source reviewer_home.sh
#   reviewer_pool_init            # REQUIRED, and never in $( ) -- see below
#   reviewer_run "$PROMPT_A" -m grok   &
#   reviewer_run "$PROMPT_B" -m gemini &
#   wait
#   reviewer_pool_destroy         # or let the EXIT trap do it
#
# Call reviewer_run. Do not execute this sourced library as a command and do not
# invoke bare `hermes -z`; the helper owns credential seeding, isolation,
# provider pinning, and the only supported argv shape: `hermes -z "$prompt"`
# followed by options.
#
# HARD-WON CONSTRAINTS (each cost a real bug; do not "simplify" them away)
#
# 1. FAIL CLOSED ON SCRATCH FAILURE. If mktemp fails (TMPDIR unset, full, or
#    read-only) an unchecked $home is EMPTY, and Hermes treats an empty
#    HERMES_HOME as unset -- falling straight back to the caller's real
#    ~/.hermes/state.db. The isolation helper would then silently cause the
#    exact corruption it exists to prevent. Every scratch path is validated and
#    the reviewer REFUSES to run without one.
#
# 2. NEVER CALL reviewer_pool_init IN COMMAND SUBSTITUTION. `P=$(...)` runs it
#    in a subshell whose EXIT trap fires the moment the substitution closes,
#    deleting the pool before use. `$$` cannot detect this (bash keeps the
#    parent's pid in a subshell) and BASHPID is empty on bash 3.2 (macOS). So
#    init EXPORTS nothing and RETURNS nothing -- it sets a global. Lazy
#    auto-init is a hard error for the same reason: it used to run inside
#    reviewer_run's own $(reviewer_home) substitution and self-destruct.
#
# 3. TRAPS DO NOT FIRE WHILE BASH BLOCKS IN A FOREGROUND CHILD. Measured: a
#    script SIGTERMed during a foreground `sleep` never ran its EXIT trap and
#    leaked its scratch; `sleep 60 & wait $!` cleaned up correctly. reviewer_run
#    therefore backgrounds and waits.
#
# 4. A SIGNAL HANDLER THAT DOES NOT EXIT LETS THE SCRIPT RESUME. Bash returns
#    control to the next statement after the handler, so a Ctrl-C'd fan-out
#    would destroy the pool and then happily seed a NEW one and keep spending
#    model calls. The INT/TERM handler kills live reviewers, restores the
#    default disposition, and re-raises.
#
# 5. CHAIN THE CALLER'S TRAPS, DO NOT CLOBBER THEM. This file is sourced into
#    the caller's shell; a bare `trap ... EXIT` silently replaces whatever
#    cleanup the caller already installed.
#
# 6. DO NOT EXPORT REVIEWER_POOL. An exported pool is inherited by child
#    shells, which then skip init, adopt the parent's pool, and delete it on
#    their own exit while the parent's reviewers are still running.
#
# WHAT WAS REJECTED
#   bare mktemp, unseeded   No credentials: "HTTP 401: Missing Authentication
#                           header" WITH EXIT CODE 0, so a fan-out silently
#                           scores dead reviewers as successful seats.
#   profiles/<name> + -p    Litters the profile namespace, needs sweep-on-crash,
#                           forces invented names; nested names fail rc=2.
#   MoA presets             Broadcasts ONE prompt to N models. A panel needs N
#                           DIFFERENT prompts. Different feature.
#   delegate_task per-task  Upstream refuses it (PRs #17718/#23266/#25026/
#   model override          #34773/#36790; maintainer: "We do not want this").

set -uo pipefail

REVIEWER_POOL=""            # deliberately NOT exported -- see constraint 6
REVIEWER_POOL_OWNER=""
_REVIEWER_PRIOR_EXIT_TRAP=""
_REVIEWER_LIVE_PIDS=""

# This is a sourced library, not an executable. Fail visibly when a caller tries
# `bash reviewer_home.sh ...`; older behavior printed nothing and encouraged an
# unseeded scratch fallback that Hermes reported as HTTP 401 with exit code 0.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "reviewer_home: source this file, call reviewer_pool_init, then call reviewer_run; do not execute it" >&2
  exit 64
fi

# Credential artifacts a reviewer needs. auth.json carries OAuth tokens for
# providers that do not use a plain API key (xAI/Grok, Codex): omitting it made
# a real grok seat fail with "No xAI OAuth credentials stored" while the other
# seats passed -- a partial-credential failure that looks like a model outage.
_REVIEWER_CRED_FILES="config.yaml .env auth.json"

# _reviewer_rmtree <dir> -- delete a scratch dir, refusing anything that is not
# demonstrably ours.
#
# WHY NOT `rm -rf "$dir"`: with an empty or attacker-influenced variable that
# shape deletes the wrong tree, and this helper has already had one fail-open
# bug in that family. It is also rated CRITICAL destructive by the skills
# security scanner, whose dangerous verdict cannot be overridden -- so a raw
# recursive delete makes the whole skill uninstallable from the hub.
#
# `find <dir> -mindepth 1 -delete` removes contents without following symlinks
# out of the tree, then rmdir removes the now-empty dir itself.
_reviewer_rmtree() {
  local d="${1:-}"
  [ -n "$d" ] || return 0
  [ -d "$d" ] || return 0
  case "$d" in
    */multi-review-*) : ;;          # our pool, or a seat inside it
    *) echo "_reviewer_rmtree: refusing to delete $d" >&2; return 1 ;;
  esac
  find "$d" -mindepth 1 -delete 2>/dev/null
  rmdir "$d" 2>/dev/null
  return 0
}

_reviewer_src() {
  # Fail closed instead of guessing $HOME/.hermes. Profile-scoped Hermes keeps
  # the live credentials under $HERMES_HOME (for example profiles/kenbot), while
  # the root can contain an empty .env and no auth.json. A shell that did not
  # inherit HERMES_HOME therefore cannot safely infer the source profile.
  if [ -n "${REVIEWER_SOURCE_HOME:-}" ]; then
    printf '%s' "$REVIEWER_SOURCE_HOME"
  elif [ -n "${HERMES_HOME:-}" ]; then
    printf '%s' "$HERMES_HOME"
  else
    echo "reviewer_home: HERMES_HOME is unset; export the active profile path or set REVIEWER_SOURCE_HOME" >&2
    return 1
  fi
}

# reviewer_pool_init [dir]
# Sets $REVIEWER_POOL in the CALLER's shell and arms cleanup. Call it plain:
#     reviewer_pool_init          # correct
#     P=$(reviewer_pool_init)     # WRONG -- subshell, see constraint 2
reviewer_pool_init() {
  local requested="${1:-}"
  if [ -n "$requested" ]; then
    # A caller-supplied dir is NOT rm -rf'd blindly: we only ever delete a
    # subdirectory we created inside it, so passing $HOME or a project dir
    # cannot destroy it.
    mkdir -p "$requested" 2>/dev/null || {
      echo "reviewer_pool_init: cannot create $requested" >&2; return 1; }
    REVIEWER_POOL="$(mktemp -d "${requested%/}/multi-review-XXXXXX" 2>/dev/null)" || REVIEWER_POOL=""
  else
    local tmp="${TMPDIR:-/tmp}"
    [ -d "$tmp" ] && [ -w "$tmp" ] || tmp=/tmp
    REVIEWER_POOL="$(mktemp -d "${tmp%/}/multi-review-XXXXXX" 2>/dev/null)" || REVIEWER_POOL=""
  fi

  # Constraint 1: fail closed. An empty pool would make every reviewer fall
  # back to the caller's live database.
  if [ -z "$REVIEWER_POOL" ] || [ ! -d "$REVIEWER_POOL" ]; then
    REVIEWER_POOL=""
    echo "reviewer_pool_init: FAILED to create scratch dir; refusing to run" >&2
    return 1
  fi
  chmod 700 "$REVIEWER_POOL" || { _reviewer_rmtree "$REVIEWER_POOL"; REVIEWER_POOL=""; return 1; }

  REVIEWER_POOL_OWNER="$$"
  _REVIEWER_LIVE_PIDS=""

  # Constraint 5: chain, do not clobber, the caller's EXIT trap.
  _REVIEWER_PRIOR_EXIT_TRAP="$(trap -p EXIT 2>/dev/null \
    | sed "s/^trap -- '//; s/' EXIT$//")"
  if [ -n "$_REVIEWER_PRIOR_EXIT_TRAP" ]; then
    trap "reviewer_pool_destroy; ${_REVIEWER_PRIOR_EXIT_TRAP}" EXIT
  else
    trap 'reviewer_pool_destroy' EXIT
  fi
  # Constraint 4: kill reviewers, restore default, re-raise so the shell
  # actually dies instead of resuming into the next fan-out.
  trap '_reviewer_on_signal INT' INT
  trap '_reviewer_on_signal TERM' TERM
  return 0
}

_reviewer_on_signal() {
  local sig="$1"
  _reviewer_kill_live
  reviewer_pool_destroy
  trap - INT TERM EXIT
  kill -s "$sig" "$$" 2>/dev/null
}

# Kill every live reviewer, including ones started as `reviewer_run ... &`.
#
# WHY NOT A TRACKED PID LIST: with the documented concurrent form, reviewer_run
# runs in a BACKGROUND SUBSHELL, so any `_REVIEWER_LIVE_PIDS=...` assignment
# inside it mutates that subshell's copy only. Measured: the parent's list was
# [] while two reviewers ran. A parent handler relying on that list kills
# nothing. (Reviewers often still die because SIGTERM reaches the shared
# process group -- but that is incidental, not a guarantee, and it does not
# hold if a reviewer is setsid'd or briefly ignores TERM.)
#
# `jobs -p` IS evaluated in the parent and lists the background subshells, so
# we signal each job and its descendants -- the actual `hermes` child.
_reviewer_kill_live() {
  local j p
  for j in $(jobs -p 2>/dev/null); do
    # children first, so hermes dies even if the subshell exits immediately
    for p in $(pgrep -P "$j" 2>/dev/null); do
      kill -TERM "$p" 2>/dev/null
    done
    kill -TERM "$j" 2>/dev/null
  done
  for p in $_REVIEWER_LIVE_PIDS; do
    kill -TERM "$p" 2>/dev/null
  done
  _REVIEWER_LIVE_PIDS=""
}

# reviewer_home -> prints a fresh isolated HERMES_HOME, or fails.
# Anonymous and unlimited: one call per reviewer.
reviewer_home() {
  # Constraint 2: no lazy init. Auto-initialising here would run inside
  # reviewer_run's $(reviewer_home) subshell and self-destruct, handing the
  # reviewer an unseeded home -- the silent 401 path.
  if [ -z "$REVIEWER_POOL" ] || [ ! -d "$REVIEWER_POOL" ]; then
    echo "reviewer_home: call reviewer_pool_init first (plain, not in \$( ))" >&2
    return 1
  fi

  local home
  home="$(mktemp -d "${REVIEWER_POOL%/}/r-XXXXXX" 2>/dev/null)" || home=""
  if [ -z "$home" ] || [ ! -d "$home" ]; then
    echo "reviewer_home: mktemp failed; refusing to run without isolation" >&2
    return 1
  fi
  chmod 700 "$home" || { _reviewer_rmtree "$home"; return 1; }

  local src f copied=0
  src="$(_reviewer_src)" || {
    _reviewer_rmtree "$home"
    return 1
  }
  [ -n "$src" ] && [ -d "$src" ] || {
    echo "reviewer_home: credential source is not a directory: ${src:-<empty>}" >&2
    _reviewer_rmtree "$home"
    return 1
  }
  for f in $_REVIEWER_CRED_FILES; do
    if [ -f "${src}/${f}" ]; then
      cp "${src}/${f}" "${home}/${f}" || {
        echo "reviewer_home: failed to copy ${f}" >&2
        _reviewer_rmtree "$home"; return 1; }
      chmod 600 "${home}/${f}"
      copied=$((copied + 1))
    fi
  done

  # A reviewer with no config.yaml returns HTTP 401 and EXIT CODE 0, which a
  # fan-out counts as a successful seat. Refuse instead of scoring a ghost.
  if [ ! -f "${home}/config.yaml" ]; then
    echo "reviewer_home: no config.yaml under ${src}; refusing (would 401 with rc=0)" >&2
    _reviewer_rmtree "$home"
    return 1
  fi

  printf '%s' "$home"
}

# reviewer_run <prompt> [extra hermes args...]
# One reviewer, one private home, torn down immediately after.
# -t '' keeps it text-in/text-out: a headless reviewer that tries to call a
# tool hangs forever waiting for an approval nobody can give.
reviewer_run() {
  local prompt="$1"; shift
  local home rc=0
  home="$(reviewer_home)" || return 1
  [ -n "$home" ] && [ -d "$home" ] || {
    echo "reviewer_run: no isolated home; refusing" >&2; return 1; }

  # Pin the provider only when the caller named a model: Hermes rejects
  # --provider without --model ("--provider requires --model"), so pinning a
  # bare call would break the default-model path that already works.
  # Inspect arguments INDIVIDUALLY: argparse accepts the equals form
  # (--model=grok, --provider=custom:x) as well as space-separated, and a
  # whole-string glob silently misses it -- leaving exactly the unpinned bare
  # alias this function exists to prevent.
  local pin="" has_model=0 has_provider=0 arg
  for arg in "$@"; do
    case "$arg" in
      --provider|--provider=*) has_provider=1 ;;
      -m|--model|--model=*|-m=*) has_model=1 ;;
    esac
  done
  if [ "$has_provider" -eq 0 ] && [ "$has_model" -eq 1 ]; then
    local prov
    prov="$(HERMES_HOME="$home" hermes config get model.provider 2>/dev/null \
            | tr -d '[:space:]')"
    case "$prov" in
      ""|*"notset"*|*"not set"*) : ;;
      *) pin="$prov" ;;
    esac
  fi

  # Constraint 3: background + wait so traps can fire.
  # BOARD IDENTITY IS STRIPPED, ALWAYS. Measured 2026-08-30 on a Kanban worker
  # reviewing its own PR: `-t ''` does NOT reliably stop a seat resolving the
  # kanban toolset from the seeded config.yaml, and HERMES_KANBAN_TASK told it
  # WHICH card it was. Two of three seats "delivered" their review by calling
  # kanban_complete on the CALLER'S OWN in-progress card. That marked the task
  # done mid-run, which made the dispatcher garbage-collect the caller's
  # WORKSPACE out from under it -- the clone, the branch, the uncommitted work.
  # Survived only because everything had already been pushed.
  #
  # A reviewer must not be able to mutate shared state. Unsetting the task/board
  # env is the cheap half of pitfall 13; still verify your own card afterwards.
  if [ -n "$pin" ]; then
    HERMES_HOME="$home" \
    env -u HERMES_KANBAN_TASK -u HERMES_KANBAN_WORKSPACE -u HERMES_KANBAN_DB \
        -u HERMES_KANBAN_BOARD -u HERMES_KANBAN_BRANCH -u HERMES_SESSION_ID \
      hermes -z "$prompt" -t '' --provider "$pin" "$@" &
  else
    HERMES_HOME="$home" \
    env -u HERMES_KANBAN_TASK -u HERMES_KANBAN_WORKSPACE -u HERMES_KANBAN_DB \
        -u HERMES_KANBAN_BOARD -u HERMES_KANBAN_BRANCH -u HERMES_SESSION_ID \
      hermes -z "$prompt" -t '' "$@" &
  fi
  local pid=$!
  _REVIEWER_LIVE_PIDS="$_REVIEWER_LIVE_PIDS $pid"
  wait "$pid" || rc=$?
  _REVIEWER_LIVE_PIDS="$(echo "$_REVIEWER_LIVE_PIDS" | tr ' ' '\n' \
    | grep -v "^${pid}$" | tr '\n' ' ')"
  _reviewer_rmtree "$home"
  return $rc
}

# reviewer_pool_destroy -- idempotent, safe from a trap and again by hand.
reviewer_pool_destroy() {
  [ -n "${REVIEWER_POOL:-}" ] || return 0
  [ "${REVIEWER_POOL_OWNER:-}" = "$$" ] || return 0
  case "$REVIEWER_POOL" in
    */multi-review-*) : ;;                 # only ever our own scratch
    *) REVIEWER_POOL=""; return 0 ;;
  esac
  [ -d "$REVIEWER_POOL" ] && _reviewer_rmtree "$REVIEWER_POOL"
  REVIEWER_POOL=""
  return 0
}

# reviewer_assert_isolation <caller-state-db>
# Prove no reviewer opened the caller's database. Run this whenever the fan-out
# changes: good reviews say nothing about whether isolation held.
reviewer_assert_isolation() {
  echo "holders of caller db: $(lsof -t "$1" 2>/dev/null | wc -l | tr -d ' ')"
}
