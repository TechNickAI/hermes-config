#!/usr/bin/env bash
# Set up BlueBubbles as the iMessage bridge for a Hermes fleet member.
#
# Automates every step that CAN be automated, and stops with clear
# instructions at each step that genuinely requires a human at the GUI
# (macOS permission grants cannot be scripted -- SIP protects TCC.db).
#
# Usage:
#   ./setup-bluebubbles.sh            # install + guide + verify
#   ./setup-bluebubbles.sh --verify   # verify an existing install only
#   ./setup-bluebubbles.sh --wire     # write Hermes env config only
#
# Env:
#   BB_PASSWORD       server password (prompted if unset)
#   BB_PORT           BlueBubbles server port (default 1234)
#   HERMES_ENV        path to the Hermes .env (default ~/.hermes/.env)

set -uo pipefail   # NOTE: deliberately no -e; see pitfalls in SKILL.md

BB_PORT="${BB_PORT:-1234}"
HERMES_ENV="${HERMES_ENV:-$HOME/.hermes/.env}"
BB_APP="/Applications/BlueBubbles.app"
BB_SUPPORT="$HOME/Library/Application Support/bluebubbles-server"

c_ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
c_warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
c_fail() { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
c_act()  { printf '\n\033[36m>> YOU MUST DO THIS AT THE MAC:\033[0m %s\n' "$1"; }
hdr()    { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# Single source of truth for "is the server app up?". Matches the full
# executable path, not the bundle name, so a stray Finder/installer window
# or a `grep BlueBubbles` in another pipeline cannot register as a hit.
bb_running() { pgrep -f "BlueBubbles.app/Contents/MacOS/BlueBubbles" >/dev/null 2>&1; }

# ---------------------------------------------------------------- preflight
preflight() {
  hdr "Preflight"
  local fatal=0

  if [[ "$(uname -s)" != "Darwin" ]]; then
    c_fail "not macOS. BlueBubbles requires a Mac."; return 1
  fi
  c_ok "macOS $(sw_vers -productVersion) ($(uname -m))"

  # Messages.app must be signed in -- chat.db only exists once it is.
  if [[ -f "$HOME/Library/Messages/chat.db" ]]; then
    c_ok "Messages.app is signed in (chat.db present)"
  else
    c_fail "chat.db missing -- open Messages.app and sign into iMessage first"
    fatal=1
  fi

  # SIP is OPTIONAL. Only Private API extras need it disabled.
  if csrutil status 2>/dev/null | grep -q "disabled"; then
    c_ok "SIP disabled -- Private API extras (tapbacks, typing) available"
  else
    c_warn "SIP enabled -- send/receive work fine; tapbacks+typing unavailable."
    c_warn "  This is the RECOMMENDED posture for non-technical users' Macs."
  fi

  if ! command -v brew >/dev/null 2>&1; then
    c_warn "Homebrew missing -- will need the manual DMG install"
  fi

  # Port must be free or already ours.
  local holder
  holder=$( { lsof -nP -iTCP:"$BB_PORT" -sTCP:LISTEN 2>/dev/null || true; } | awk 'NR==2{print $1}')
  if [[ -z "$holder" ]]; then
    c_ok "port $BB_PORT free"
  elif [[ "$holder" == *BlueBubbles* ]]; then
    c_ok "port $BB_PORT already served by BlueBubbles"
  else
    c_fail "port $BB_PORT held by '$holder' -- set BB_PORT to something else"
    fatal=1
  fi

  return $fatal
}

# ---------------------------------------------------------------- install
install_app() {
  hdr "Install BlueBubbles"

  if [[ -d "$BB_APP" ]]; then
    local v
    v=$(defaults read "$BB_APP/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo "?")
    c_ok "already installed (v$v)"
    return 0
  fi

  # The Homebrew cask is deprecated (fails Gatekeeper, disabled 2026-09-01).
  # Prefer it while it exists because it is scriptable, else send them to the DMG.
  if command -v brew >/dev/null 2>&1 && brew install --cask bluebubbles 2>&1 | tail -3; then
    if [[ -d "$BB_APP" ]]; then c_ok "installed via Homebrew"; return 0; fi
  fi

  c_fail "automated install unavailable"
  c_act "Download and install from https://bluebubbles.app/downloads/ then re-run"
  return 1
}

# ---------------------------------------------------------------- gatekeeper
# BlueBubbles is signed with a Developer ID but NOT notarized by Apple.
# macOS tags every download with com.apple.quarantine, and for an
# un-notarized app that tag produces "BlueBubbles is damaged / can't be
# opened" -- the app silently refuses to launch. Clearing the tag on this
# one bundle is the standard fix and does NOT weaken Gatekeeper globally.
# This is also why the Homebrew cask is deprecated (disabled 2026-09-01).
clear_quarantine() {
  hdr "Gatekeeper"

  if ! xattr -l "$BB_APP" 2>/dev/null | grep -q "com.apple.quarantine"; then
    c_ok "no quarantine flag set"
  elif xattr -dr com.apple.quarantine "$BB_APP" 2>/dev/null; then
    c_ok "cleared com.apple.quarantine (app was un-notarized)"
  else
    c_fail "could not clear quarantine flag"
    c_act "System Settings > Privacy & Security > scroll down > 'Open Anyway'"
    return 1
  fi

  # spctl still reports 'rejected' for un-notarized apps even once the
  # quarantine tag is gone. That is expected and does not block launch --
  # report it as informational so nobody chases a non-problem.
  if ! spctl -a "$BB_APP" >/dev/null 2>&1; then
    c_warn "spctl reports un-notarized (expected for BlueBubbles; launches fine)"
  fi
}

# ---------------------------------------------------------------- permissions
check_permissions() {
  hdr "Permissions (human required)"

  # TCC.db is SIP-protected and unreadable even under sudo, so we cannot
  # verify grants by query. We verify by OBSERVED BEHAVIOR instead: if the
  # server answers /api/v1/ping and returns chats, FDA is working.
  c_warn "macOS permission grants cannot be scripted -- SIP protects the TCC database"
  c_act "In BlueBubbles, complete first-run setup:
     1. Set a SERVER PASSWORD (save it -- Hermes needs it)
     2. Grant FULL DISK ACCESS when prompted
        System Settings > Privacy & Security > Full Disk Access > enable BlueBubbles
     3. Leave 'Private API' OFF unless SIP is disabled on this Mac
     4. Confirm the server shows a green/connected status"
}

# ---------------------------------------------------------------- wire hermes
wire_hermes() {
  hdr "Wire Hermes"

  if [[ -z "${BB_PASSWORD:-}" ]]; then
    read -r -s -p "  BlueBubbles server password: " BB_PASSWORD; echo
  fi
  if [[ -z "$BB_PASSWORD" ]]; then
    c_fail "no password given -- cannot wire Hermes"; return 1
  fi

  mkdir -p "$(dirname "$HERMES_ENV")"
  touch "$HERMES_ENV"
  # cp preserves the SOURCE mode: a pre-existing 644 .env would produce a
  # world-readable backup containing credentials. Force 600 on the copy.
  local bak="$HERMES_ENV.bak.$(date +%Y%m%d%H%M%S)"
  cp "$HERMES_ENV" "$bak"
  chmod 600 "$bak"

  # Idempotent: strip prior BLUEBUBBLES_* lines, then append fresh block.
  local tmp; tmp=$(mktemp)
  grep -v '^BLUEBUBBLES_' "$HERMES_ENV" > "$tmp" 2>/dev/null || true
  {
    echo ""
    echo "# BlueBubbles (iMessage) -- added $(date +%Y-%m-%d)"
    echo "BLUEBUBBLES_SERVER_URL=http://127.0.0.1:${BB_PORT}"
    echo "BLUEBUBBLES_PASSWORD=${BB_PASSWORD}"
    echo "BLUEBUBBLES_WEBHOOK_HOST=127.0.0.1"
    echo "BLUEBUBBLES_WEBHOOK_PORT=8645"
    echo "BLUEBUBBLES_WEBHOOK_PATH=/bluebubbles-webhook"
    # Conservative inbound defaults. REQUIRE_MENTION=false would let the agent
    # answer every message in every thread, including group chats, and read
    # receipts silently mark a human's messages read on their behalf. Both are
    # opt-in, not opt-out.
    echo "BLUEBUBBLES_SEND_READ_RECEIPTS=false"
    echo "BLUEBUBBLES_REQUIRE_MENTION=true"
  } >> "$tmp"
  mv "$tmp" "$HERMES_ENV"
  chmod 600 "$HERMES_ENV"

  c_ok "wrote BLUEBUBBLES_* to $HERMES_ENV (backup alongside, mode 600)"
  c_warn "restart the Hermes gateway for the adapter to load"
}

# ---------------------------------------------------------------- verify
verify() {
  hdr "Verify"
  local url="http://127.0.0.1:${BB_PORT}"
  local pw="${BB_PASSWORD:-}"

  if [[ -z "$pw" && -f "$HERMES_ENV" ]]; then
    pw=$( { grep '^BLUEBUBBLES_PASSWORD=' "$HERMES_ENV" || true; } | head -1 | cut -d= -f2-)
  fi

  if ! bb_running; then
    c_fail "BlueBubbles is not running -- open -a BlueBubbles"
    return 1
  fi
  c_ok "BlueBubbles process running"

  if [[ -z "$pw" ]]; then
    c_fail "no server password known -- cannot test the API"; return 1
  fi

  # ping: proves the server is listening AND the password is right.
  # Do NOT grep the body for "message" -- BlueBubbles' 401 response is
  # {"status":401,"message":"You are not authorized..."} which contains
  # "message", so a wrong password would be reported as success. Assert on
  # the HTTP status code instead.
  local ping_code
  ping_code=$(curl -sS -m 10 -o /tmp/bb-ping.$$ -w '%{http_code}' \
                "${url}/api/v1/ping?password=${pw}" 2>/dev/null)
  if [[ "$ping_code" == "200" ]]; then
    c_ok "API ping succeeded"
  elif [[ "$ping_code" == "401" ]]; then
    c_fail "API ping rejected: wrong server password"
    rm -f /tmp/bb-ping.$$
    return 1
  else
    c_fail "API ping failed (HTTP ${ping_code:-000})"
    c_warn "  server may not have finished first-run setup"
    rm -f /tmp/bb-ping.$$
    return 1
  fi
  rm -f /tmp/bb-ping.$$

  # An auth check that cannot FAIL proves nothing: confirm a deliberately
  # wrong password is actually rejected, so we know auth is switched on.
  local bad_code
  bad_code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' \
               "${url}/api/v1/ping?password=deliberately-wrong-$$" 2>/dev/null)
  if [[ "$bad_code" == "401" ]]; then
    c_ok "auth is enforced (wrong password rejected)"
  else
    c_fail "auth NOT enforced: wrong password returned HTTP ${bad_code:-000}"
    c_warn "  anyone who reaches this port can read every message"
    return 1
  fi

  # server/info: proves FDA is working and reports Private API state.
  local info
  info=$(curl -sS -m 10 "${url}/api/v1/server/info?password=${pw}" 2>&1)
  if echo "$info" | grep -q 'private_api'; then
    local papi
    papi=$(echo "$info" | grep -o '"private_api":[a-z]*' | head -1 | cut -d: -f2)
    c_ok "server/info reachable (private_api=${papi:-unknown})"
  else
    c_warn "server/info did not report private_api"
  fi

  # chat/query is the REAL Full Disk Access test -- it reads chat.db.
  local chats
  chats=$(curl -sS -m 15 -X POST "${url}/api/v1/chat/query?password=${pw}" \
            -H 'Content-Type: application/json' -d '{"limit":1,"offset":0}' 2>&1)
  if echo "$chats" | grep -q '"data"'; then
    c_ok "chat/query returned data -- Full Disk Access is working"
  else
    c_fail "chat/query failed -- Full Disk Access likely NOT granted"
    c_warn "  ${chats:0:200}"
    c_act "System Settings > Privacy & Security > Full Disk Access > enable BlueBubbles, then restart it"
    return 1
  fi

  check_tcc

  # Exposure is a REQUIRED check, not advisory. Ignoring its exit status made
  # --verify print "healthy" while a public tunnel still served the whole
  # message archive.
  if ! check_exposure; then
    hdr "Result"
    c_fail "BlueBubbles works, but the server is PUBLICLY EXPOSED"
    c_warn "  close the tunnel (see above), then re-run --verify"
    return 1
  fi

  hdr "Result"
  c_ok "BlueBubbles is healthy, reachable by Hermes, and not publicly exposed"
}

# ---------------------------------------------------------------- tcc state
# Report what macOS has ACTUALLY granted, rather than asking the user to hunt
# through System Settings. Key subtlety: an app is absent from the Full Disk
# Access list until it first tries to read a protected file, so "not in the
# list" means "never asked", NOT "user missed it".
check_tcc() {
  hdr "Permissions (actual grants)"
  local tccdb="/Library/Application Support/com.apple.TCC/TCC.db"

  # TCC.db is SIP-protected; readable only with sudo, and never writable.
  local rows
  rows=$(sudo -n sqlite3 "$tccdb" \
    "select service, auth_value from access where client like '%luebubble%';" 2>/dev/null)

  if [[ -z "$rows" ]]; then
    c_warn "no TCC grants recorded for BlueBubbles yet"
    c_warn "  (expected before first-run completes -- the app must ASK first)"
    return 0
  fi

  local fda="" acc=""
  while IFS='|' read -r svc val; do
    case "$svc" in
      kTCCServiceSystemPolicyAllFiles) fda="$val" ;;
      kTCCServiceAccessibility)        acc="$val" ;;
    esac
  done <<< "$rows"

  case "$fda" in
    2) c_ok   "Full Disk Access: granted (explicit TCC entry)" ;;
    0) c_fail "Full Disk Access: DENIED -- enable it and restart BlueBubbles" ;;
    *) c_warn "Full Disk Access: no explicit TCC entry.
        This does NOT mean access is broken -- an Electron app can inherit
        disk access from an already-authorized parent (e.g. Terminal), so
        chat reads may work anyway. The authoritative test is a real
        chat/query call, not this table. Run --verify." ;;
  esac

  case "$acc" in
    2) c_ok   "Accessibility: granted (Private API extras available)" ;;
    *) c_warn "Accessibility: not granted (tapbacks/typing unavailable; optional)" ;;
  esac
}

# ---------------------------------------------------------------- exposure
# BlueBubbles defaults to opening a public Cloudflare/ngrok tunnel so phone
# clients can reach it from anywhere. Hermes talks to it over LOOPBACK, so
# for a fleet agent that tunnel is pure attack surface: a public URL fronting
# every message the owner has ever sent, guarded only by one password.
check_exposure() {
  hdr "Network exposure"
  local db="$HOME/Library/Application Support/bluebubbles-server/config.db"
  [[ -f "$db" ]] || { c_warn "no config.db yet"; return 0; }

  local addr proxy
  addr=$(sqlite3 "$db" "select value from config where name='server_address';" 2>/dev/null)
  proxy=$(sqlite3 "$db" "select value from config where name='proxy_service';" 2>/dev/null)

  if [[ "$addr" == http://localhost* || "$addr" == http://127.0.0.1* ]]; then
    c_ok "server is loopback-only ($addr)"
  elif [[ -n "$addr" ]]; then
    c_fail "server is PUBLICLY EXPOSED via ${proxy:-a tunnel}: $addr"
    c_warn "  Hermes only needs loopback. A public URL fronts the whole message"
    c_warn "  history behind a single password."
    c_act "In BlueBubbles > Settings > Connection, set Proxy Service to
     'Dynamic DNS' and the address to http://localhost:${BB_PORT},
     or disable the tunnel entirely. Then re-run --verify."
    return 1
  fi
}

# ---------------------------------------------------------------- main
main() {
  case "${1:-}" in
    --verify) verify; exit $? ;;
    --wire)   wire_hermes; exit $? ;;
    --exposure) check_exposure; exit $? ;;
    --perms)  check_tcc; exit $? ;;
  esac

  preflight || { c_fail "preflight failed -- fix the above first"; exit 1; }
  install_app || exit 1
  clear_quarantine || exit 1

  if ! bb_running; then
    open -a BlueBubbles
    sleep 5
  fi

  check_permissions
  echo
  read -r -p "Press Enter once first-run setup is complete... " _
  wire_hermes || exit 1
  verify
}

main "$@"
