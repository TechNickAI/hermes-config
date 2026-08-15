# Renaming the mini-app framework root directory

Nick's rule: **NO symlinks for path migrations.** Symlinks rot and hide drift. Do the real rename and update every reference.

This is the playbook used 2026-05-27 to rename `~/openclaw-apps` → `~/mini-apps`. Same shape applies to any future framework root move.

## Pre-flight: find every reference BEFORE you touch anything

Don't run a recursive grep over `/Users/<user>` — it'll hit node_modules and time out (180s+). Be surgical:

```bash
# 1. In-dir live config files (the ones that matter)
grep -l "OLDNAME" \
  /Users/<user>/OLDNAME/ecosystem.config.js \
  /Users/<user>/OLDNAME/_registry/Caddyfile \
  /Users/<user>/OLDNAME/_registry/apps.json \
  /Users/<user>/OLDNAME/_registry/tailscale-serve.json \
  /Users/<user>/OLDNAME/_registry/apply-tailscale-serve.sh \
  /Users/<user>/OLDNAME/README.md \
  /Users/<user>/OLDNAME/auth-service/server.js

# 2. Outside refs (skills + PM2 state)
grep -l "OLDNAME" /Users/<user>/.hermes/profiles/bosun/skills/mini-app/SKILL.md
grep -c "OLDNAME" /Users/<user>/.pm2/dump.pm2

# 3. Launchd/cron/shellrc — usually empty, but check
ls /Users/<user>/Library/LaunchAgents/ | grep -i OLDNAME
crontab -l | grep OLDNAME
grep -l "OLDNAME" /Users/<user>/.zshrc /Users/<user>/.zprofile

# 4. Confirm Caddy supervisor (you'll need to restart it the same way)
ps -p $(pgrep -f "caddy run" | head -1) -o ppid=,command=
# If ppid=1 → started via nohup/launchd. If a launchd plist exists, use it.
# If no plist, you'll restart it as a background process (see below).
```

Historical journal/memory files (e.g. `.openclaw/workspace-sterling/memory/**`) — **don't patch**. Those are dated notes, not active config. Let them age.

## The rename

```bash
# 1. Stop everything that holds the path open
PM2_HOME=/Users/<user>/.pm2 pm2 stop all
kill $(pgrep -f "caddy run" | head -1)
sleep 2 && pgrep -f "caddy run"  # verify caddy is down

# 2. Move (NOT symlink). Guard against existing target.
test -e /Users/<user>/NEWNAME && { echo "ERROR target exists"; exit 1; }
mv /Users/<user>/OLDNAME /Users/<user>/NEWNAME

# 3. Patch absolute paths in live config files
sed -i '' 's|/Users/<user>/OLDNAME|/Users/<user>/NEWNAME|g' \
  /Users/<user>/NEWNAME/ecosystem.config.js \
  /Users/<user>/NEWNAME/_registry/apps.json

# 4. Patch bare-name refs in human-readable files
sed -i '' 's|OLDNAME|NEWNAME|g' \
  /Users/<user>/NEWNAME/_registry/tailscale-serve.json \
  /Users/<user>/NEWNAME/_registry/apply-tailscale-serve.sh \
  /Users/<user>/NEWNAME/README.md

# 5. Patch PM2 dump (25+ cwd refs auto-rewritten on `pm2 save` after restart,
#    but dump is what PM2 reads on resurrect — patch it now to be safe)
sed -i '' 's|/Users/<user>/OLDNAME|/Users/<user>/NEWNAME|g' /Users/<user>/.pm2/dump.pm2

# 6. Patch outside refs (mini-app skill is the only known one)
sed -i '' 's|OLDNAME|NEWNAME|g' /Users/<user>/.hermes/profiles/bosun/skills/mini-app/SKILL.md

# 7. Verify zero stale refs
grep -l "OLDNAME" \
  /Users/<user>/NEWNAME/ecosystem.config.js \
  /Users/<user>/NEWNAME/_registry/Caddyfile \
  /Users/<user>/NEWNAME/_registry/apps.json \
  /Users/<user>/NEWNAME/_registry/tailscale-serve.json \
  /Users/<user>/NEWNAME/_registry/apply-tailscale-serve.sh \
  /Users/<user>/NEWNAME/README.md \
  /Users/<user>/.pm2/dump.pm2 \
  /Users/<user>/.hermes/profiles/bosun/skills/mini-app/SKILL.md \
  && echo FAIL || echo OK
```

## Restart

**Caddy** — must run as background process, NOT via `nohup ... &` (Hermes harness rejects shell-level backgrounding):

```python
# Use terminal(background=true, watch_patterns=["serving initial configuration"]):
/opt/homebrew/bin/caddy run --config /Users/<user>/NEWNAME/_registry/Caddyfile --adapter caddyfile
```

**PM2** — start clean from new ecosystem path, then save:

```bash
cd /Users/<user>/NEWNAME
PM2_HOME=/Users/<user>/.pm2 pm2 delete all
PM2_HOME=/Users/<user>/.pm2 pm2 start ecosystem.config.js
PM2_HOME=/Users/<user>/.pm2 pm2 save
```

## Verify

```bash
# Caddy health endpoint (defined in Caddyfile :8080 block, no auth)
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read().decode())"
# Expected: "ok"

# At least one upstream app reachable through Caddy
python3 -c "import urllib.request as u; r=u.urlopen('http://127.0.0.1:8080/hello-world/', timeout=3); print(r.status)"
# Expected: 200

# All PM2 apps online, restart_time=0 (clean boot, no crash loop)
PM2_HOME=/Users/<user>/.pm2 pm2 jlist | python3 -c "import json,sys; [print(f\"{a['name']:20} {a['pm2_env']['status']:8} restarts={a['pm2_env']['restart_time']}\") for a in json.load(sys.stdin)]"
```

If any app shows `restart_time > 0` or `status != online`, check `pm2 logs <name>` — usually a missed path patch.

## Pitfalls

- **Don't use `set -e` with a verification `ls` that's *supposed* to fail.** I had `ls -d /Users/<user>/NEWNAME /Users/<user>/OLDNAME` after the `mv` to prove the old one was gone — that `ls` exited 1 (correctly) and `set -e` killed the whole script before the patches ran. Either drop `set -e` for verifications or use `ls ... || true`.
- **Don't try `nohup ... &` in foreground mode.** Hermes harness intercepts and refuses. Use `terminal(background=true, watch_patterns=[...])` for Caddy.
- **Don't recursive-grep all of `/Users/<user>`.** Times out on node_modules. Target known config files instead.
- **Skip historical memory/journal files.** `.openclaw/workspace-*/memory/**` are dated notes; patching them rewrites history for no benefit.
- **`.next/` build artifacts will have stale paths** (e.g. `markdown-viewer/.next/required-server-files.json`). Don't patch them — they regenerate on next `next build`. PM2 restart of a Next.js app handles it.
