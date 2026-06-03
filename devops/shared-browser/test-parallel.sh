#!/usr/bin/env bash
# Parallel browser stress test for the WINDOW model: N agents each drive their
# own window against different sites at once, then we verify every window kept
# its own page (no cross-window clobber). Exercises the thundering-herd start.
set -uo pipefail

declare -a JOBS=(
  "agent1 https://example.com"
  "agent2 https://example.org"
  "agent3 https://www.iana.org"
  "agent4 https://duckduckgo.com"
  "agent5 https://www.wikipedia.org"
)

echo "=== firing ${#JOBS[@]} parallel navs (one window each) ==="
printf '%s\n' "${JOBS[@]}" | xargs -P 5 -I{} bash -c '
  set -- {}
  w=$1; u=$2
  out=$(browser nav "$u" --window "$w" 2>&1 | head -1)
  echo "[$w] $out"
'

echo "=== verifying each window kept its own URL (no clobber) ==="
fail=0
for entry in "${JOBS[@]}"; do
  w=$(echo "$entry" | awk "{print \$1}")
  want=$(echo "$entry" | awk "{print \$2}")
  got=$(browser eval "location.href" --window "$w" 2>&1 | tail -1)
  wh=$(echo "$want" | sed -E "s#https?://(www\.)?([^/]+).*#\2#")
  if echo "$got" | grep -q "$wh"; then
    echo "[$w] OK   want~$wh  got=$got"
  else
    echo "[$w] FAIL want~$wh  got=$got"; fail=1
  fi
done

echo "=== windows table ==="
browser windows
echo "=== exit: $fail (0 = all isolated correctly) ==="
exit $fail
