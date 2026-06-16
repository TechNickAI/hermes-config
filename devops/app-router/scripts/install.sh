#!/bin/bash
# Idempotent install of the Hermes mini-app router into ~/mini-apps/.
#
# Run from the repo root:
#   bash devops/app-router/scripts/install.sh
#
# Re-running is safe — existing files are not overwritten unless --force is set.
# After install, edit ~/mini-apps/ecosystem.config.js to set AUTH_SECRET and
# any per-app passwords, then start with:
#   pm2 start ~/mini-apps/ecosystem.config.js && pm2 save

set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${HERMES_MINI_APPS_DIR:-$HOME/mini-apps}"
FORCE="${FORCE:-0}"
if [ "${1:-}" = "--force" ]; then FORCE=1; fi

copy_if_absent() {
    local src="$1" dst="$2"
    if [ -e "$dst" ] && [ "$FORCE" != "1" ]; then
        echo "  skip (exists): $dst"
    else
        # Remove existing directory first — cp -R src dst nests src inside dst
        # when dst already exists as a directory.
        [ -d "$dst" ] && rm -rf "$dst"
        cp -R "$src" "$dst"
        echo "  wrote: $dst"
    fi
}

render_if_absent() {
    local src="$1" dst="$2"
    if [ -e "$dst" ] && [ "$FORCE" != "1" ]; then
        echo "  skip (exists): $dst"
    else
        if ! grep -q '<MINI_APPS_DIR>' "$src"; then
            echo "ERROR: placeholder <MINI_APPS_DIR> not found in $src" >&2
            exit 1
        fi
        local escaped_dest tmp
        escaped_dest="$(printf '%s' "$DEST" | sed 's/[&\\|]/\\&/g')"
        tmp="$(mktemp "${dst}.XXXXXX")"
        sed "s|<MINI_APPS_DIR>|${escaped_dest}|g" "$src" > "$tmp"
        mv "$tmp" "$dst"
        echo "  wrote: $dst"
    fi
}

echo "[install] source: $SRC"
echo "[install] target: $DEST"

mkdir -p "$DEST/router/logs" "$DEST/router/public"
copy_if_absent "$SRC/auth-service" "$DEST/auth-service"
render_if_absent "$SRC/templates/ecosystem.config.js.example" "$DEST/ecosystem.config.js"
render_if_absent "$SRC/templates/Caddyfile.example" "$DEST/router/Caddyfile"
copy_if_absent "$SRC/templates/index.html" "$DEST/router/public/index.html"
copy_if_absent "$SRC/scripts/apply-tailscale-serve.sh" "$DEST/router/apply-tailscale-serve.sh"
chmod +x "$DEST/router/apply-tailscale-serve.sh"
copy_if_absent "$SRC/scripts/tailscale-serve.json" "$DEST/router/tailscale-serve.json"

# Migration: remove the obsolete imperative restore script if it lingers from
# a previous install. The declarative apply script + JSON config replaces it.
if [ -f "$DEST/router/restore-tailscale-serve.sh" ]; then
    echo "  removing obsolete: $DEST/router/restore-tailscale-serve.sh"
    rm -f "$DEST/router/restore-tailscale-serve.sh"
fi

echo "[install] installing auth-service deps"
(cd "$DEST/auth-service" && npm install --omit=dev --silent --no-audit --no-fund)

LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.hermes.mini-app-router-serve.plist"
PLIST_DST="$LAUNCH_AGENT_DIR/$PLIST_NAME"
if [ -d "$LAUNCH_AGENT_DIR" ]; then
    if [ -e "$PLIST_DST" ] && [ "$FORCE" != "1" ]; then
        echo "  skip (exists): $PLIST_DST"
    else
        mkdir -p "$LAUNCH_AGENT_DIR"
        escaped_home="$(printf '%s' "$HOME" | sed 's/[&\\|]/\\&/g')"
        sed "s|<HOME_DIR>|${escaped_home}|g" "$SRC/launchd/$PLIST_NAME" > "$PLIST_DST"
        echo "  wrote: $PLIST_DST"
        echo "  load with: launchctl bootstrap gui/\$(id -u) $PLIST_DST"
    fi
else
    echo "  skip launchd plist (not on macOS)"
fi

cat <<EOF

Next steps:
  1. Edit $DEST/ecosystem.config.js — set AUTH_SECRET (openssl rand -hex 32)
     and any APP_PASSWORD_<SLUG> / APP_TITLE_<SLUG> / APP_DESC_<SLUG> entries.
  2. Edit $DEST/router/Caddyfile to match.
  3. pm2 start $DEST/ecosystem.config.js && pm2 save
  4. pm2 start /opt/homebrew/bin/caddy --name caddy --interpreter none -- \\
       run --config $DEST/router/Caddyfile --adapter caddyfile
  5. Edit $DEST/router/tailscale-serve.json to declare your serve layout,
     then apply it:
         $DEST/router/apply-tailscale-serve.sh
     IMPORTANT: if another gateway on this host manages Tailscale Serve,
     disable that integration before it restarts, or it may reset this config.
EOF
