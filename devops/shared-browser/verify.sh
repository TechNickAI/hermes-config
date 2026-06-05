#!/usr/bin/env bash
# verify-browser-stack.sh — health check for the shared browser (browserd).
# Exit 0 = healthy. Run after any change to browserd.mjs or the browser CLI.
set -uo pipefail
export HOME="${HOME}"
BROWSER="${BROWSER:-browser}"
fail=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fail=1; }

echo "== shared browser health =="

# 1. CLI on PATH
command -v "$BROWSER" >/dev/null && ok "browser CLI on PATH" || bad "browser CLI not on PATH"

# 2. Deep preflight (daemon up + live nav round-trip)
if "$BROWSER" preflight >/dev/null 2>&1; then ok "preflight (daemon + live nav)"; else bad "preflight failed"; fi

# 3. Real Chrome, clean UA (no HeadlessChrome)
ua="$("$BROWSER" nav about:blank --window __verify__ >/dev/null 2>&1; "$BROWSER" eval 'navigator.userAgent' --window __verify__ 2>/dev/null)"
echo "$ua" | grep -q "Chrome/" && ! echo "$ua" | grep -qi "Headless" && ok "clean Chrome UA" || bad "UA not clean: $ua"

# 4. webdriver flag hidden
wd="$("$BROWSER" eval 'navigator.webdriver' --window __verify__ 2>/dev/null)"
[ "$wd" = "false" ] && ok "navigator.webdriver=false" || bad "webdriver=$wd"

# 5. Bot-detection canary: DuckDuckGo HTML search returns results
"$BROWSER" nav "https://duckduckgo.com/html/?q=hermes+browser+test" --window __verify__ >/dev/null 2>&1
n="$("$BROWSER" eval "document.querySelectorAll('.result__title').length" --window __verify__ 2>/dev/null)"
[ "${n:-0}" -gt 0 ] 2>/dev/null && ok "bot-detection dodge ($n results)" || bad "DDG returned 0 results"

# 6. Single daemon only
d="$(pgrep -f browserd.mjs 2>/dev/null | wc -l | tr -d ' ')"
[ "${d:-0}" -ge 1 ] && ok "daemon running ($d process)" || bad "no daemon"

"$BROWSER" close --window __verify__ >/dev/null 2>&1 || true
echo "== $( [ $fail -eq 0 ] && echo PASS || echo FAIL ) =="
exit $fail
