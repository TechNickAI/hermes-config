#!/usr/bin/env bash
# install.sh — set up the shared browser on this machine. Idempotent.
# Installs browserd + the browser CLI, puts `browser` on PATH, and verifies.
# No launchd, no plist, no MCP, no mcporter — the CLI lazy-starts the daemon.
set -euo pipefail

HOME_DIR="${HOME}"
BROWSER_HOME="${BROWSER_HOME:-$HOME_DIR/.hermes/shared-browser}"
BIN_DIR="${BIN_DIR:-$HOME_DIR/.local/bin}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> installing shared browser to $BROWSER_HOME"
mkdir -p "$BROWSER_HOME/logs" "$BROWSER_HOME/output" "$BIN_DIR"

install -m 0755 "$HERE/browserd.mjs" "$BROWSER_HOME/browserd.mjs"
install -m 0755 "$HERE/bin/browser" "$BROWSER_HOME/browser"

# Put the CLI on PATH via symlink so updates to BROWSER_HOME propagate.
ln -sf "$BROWSER_HOME/browser" "$BIN_DIR/browser"
echo "==> linked browser -> $BIN_DIR/browser"

# Dependency checks.
command -v node >/dev/null || { echo "FATAL: node not found"; exit 1; }
if ! node -e "require('playwright')" 2>/dev/null && \
   ! node -e "require(require('child_process').execSync('npm root -g').toString().trim()+'/playwright')" 2>/dev/null; then
  echo "==> installing playwright (npm i -g playwright)"
  npm install -g playwright >/dev/null 2>&1 || true
fi
# Ensure Playwright's bundled Chromium (Chrome-for-Testing) is available.
# Do NOT install/use the real Chrome channel as the default; it can tie automation
# to the macOS login keychain and the user's real Chrome app/profile lock.
node -e "require('playwright')" 2>/dev/null && npx playwright install chromium >/dev/null 2>&1 || true

case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) echo "NOTE: add $BIN_DIR to your PATH (e.g. in ~/.zshrc): export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

echo "==> verifying (cold start + live nav)"
"$BROWSER_HOME/browser" preflight
echo "==> done. Try:  browser nav example.com --window myagent"
