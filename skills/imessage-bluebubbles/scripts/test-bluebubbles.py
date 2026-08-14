#!/usr/bin/env python3
"""test-bluebubbles.py -- verification suite for the BlueBubbles iMessage bridge.

Exercises the bridge the way a real agent uses it and reports PASS/FAIL/SKIP
per check. Read-only by default; the send test is opt-in and requires an
explicit recipient, because sending texts a real human.

Usage:
  ./test-bluebubbles.py                     # read-only suite (safe, no messages)
  ./test-bluebubbles.py --send-to "+1555..." --from-name "YourAgent"
  ./test-bluebubbles.py --json              # machine-readable summary line

Exit codes:
  0 all checks passed (or passed with skips)
  1 one or more checks FAILED
  2 could not run (no config / server unreachable)

Design rules learned the hard way, encoded here:
  * A send that times out is UNKNOWN, not failed. Verify by reading the
    thread back after a delay; never retry blind.
  * Security checks assert the tunnel is DEAD from the public internet, not
    merely that a config value changed.
  * Every check states what it actually proves, so a green suite cannot be
    mistaken for more assurance than it earned.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests not installed: pip install requests")

ENV_PATH = Path.home() / ".hermes" / ".env"
CONFIG_DB = Path.home() / "Library/Application Support/bluebubbles-server/config.db"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


def record(status, name, detail=""):
    results.append((status, name, detail))
    color = {PASS: "\033[32m", FAIL: "\033[31m", SKIP: "\033[33m"}[status]
    print(f"  {color}{status}\033[0m  {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"          {line}")


def section(title):
    print(f"\n\033[1m== {title} ==\033[0m")


def load_config():
    url = os.getenv("BLUEBUBBLES_SERVER_URL")
    pw = os.getenv("BLUEBUBBLES_PASSWORD")
    if (not url or not pw) and ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k == "BLUEBUBBLES_SERVER_URL" and not url:
                url = v.strip()
            elif k == "BLUEBUBBLES_PASSWORD" and not pw:
                pw = v.strip()
    return (url or "").rstrip("/"), (pw or "")


def api(method, path, url, pw, timeout=20, **kw):
    sep = "&" if "?" in path else "?"
    full = f"{url}{path}{sep}password={urllib.parse.quote(pw, safe='')}"
    return requests.request(method, full, timeout=timeout, **kw)


# ----------------------------------------------------------------- checks
def check_process():
    section("Server process")
    r = subprocess.run(
        ["pgrep", "-f", "BlueBubbles.app/Contents/MacOS/BlueBubbles"],
        capture_output=True, text=True)
    if r.returncode == 0:
        record(PASS, "BlueBubbles is running", f"pid {r.stdout.split()[0]}")
        return True
    record(FAIL, "BlueBubbles is not running", "open -a BlueBubbles")
    return False


def check_auth(url, pw):
    section("Connectivity and auth")
    if not url or not pw:
        record(FAIL, "credentials configured", f"missing in env and {ENV_PATH}")
        return False

    # Wrong password must be REJECTED -- proves auth is actually enforced and
    # not silently disabled, which would make every other PASS meaningless.
    try:
        bad = api("GET", "/api/v1/ping", url, "definitely-wrong-password-xyz")
        if bad.status_code == 401:
            record(PASS, "auth is enforced (bad password rejected)")
        else:
            record(FAIL, "auth NOT enforced",
                   f"bad password returned {bad.status_code}, expected 401")
    except Exception as e:
        record(FAIL, "auth enforcement check errored", str(e)[:200])

    try:
        r = api("GET", "/api/v1/ping", url, pw)
    except requests.exceptions.ConnectionError:
        record(FAIL, "server reachable", f"connection refused at {url}")
        return False
    except Exception as e:
        record(FAIL, "server reachable", str(e)[:200])
        return False

    if r.status_code == 200:
        record(PASS, "ping with valid password", url)
        return True
    record(FAIL, "ping with valid password", f"HTTP {r.status_code}")
    return False


def check_server_info(url, pw):
    try:
        d = api("GET", "/api/v1/server/info", url, pw).json().get("data", {})
    except Exception as e:
        record(FAIL, "server/info", str(e)[:200])
        return {}
    record(PASS, "server/info",
           f"BlueBubbles {d.get('server_version')} on macOS {d.get('os_version')}, "
           f"private_api={d.get('private_api')}")
    return d


def check_disk_access(url, pw):
    section("Full Disk Access (the real test)")
    # ping passes with zero disk access; only reading chat.db proves FDA.
    try:
        r = api("POST", "/api/v1/chat/query", url, pw,
                json={"limit": 1, "offset": 0}, timeout=25)
        data = r.json().get("data", [])
    except Exception as e:
        record(FAIL, "chat/query reachable", str(e)[:200])
        return False
    if data:
        record(PASS, "chat/query returns data", "Full Disk Access is working")
        return True
    record(FAIL, "chat/query returned no chats",
           "Grant Full Disk Access to BlueBubbles, then RESTART the app")
    return False


def check_reads(url, pw):
    section("Read path")
    try:
        r = api("POST", "/api/v1/chat/query", url, pw,
                json={"limit": 25, "offset": 0, "with": ["participants"]},
                timeout=30)
        chats = r.json().get("data", [])
    except Exception as e:
        record(FAIL, "list chats", str(e)[:200])
        return None
    if not chats:
        record(FAIL, "list chats", "no chats returned")
        return None
    record(PASS, "list chats", f"{len(chats)} chats")

    guid = chats[0].get("guid")
    enc = urllib.parse.quote(guid, safe="")
    try:
        r = api("GET", f"/api/v1/chat/{enc}/message?limit=5", url, pw, timeout=30)
        msgs = r.json().get("data", [])
    except Exception as e:
        record(FAIL, "read message history", str(e)[:200])
        return guid

    if not msgs:
        record(SKIP, "read message history", "first chat has no messages")
        return guid

    # On modern macOS chat.db stores bodies in attributedBody, not text.
    # BlueBubbles decodes it; a raw sqlite reader would see NULL here.
    with_text = sum(1 for m in msgs if (m.get("text") or "").strip())
    if with_text:
        record(PASS, "read message history",
               f"{len(msgs)} messages, {with_text} with decoded text")
    else:
        record(FAIL, "message text is empty",
               "attributedBody decoding may be broken")
    return guid


def check_pagination(url, pw):
    # chat/query caps at 1000 rows; agents that ignore this silently miss
    # recipients and conclude a person "has no thread".
    try:
        a = api("POST", "/api/v1/chat/query", url, pw,
                json={"limit": 1000, "offset": 0}, timeout=40).json().get("data", [])
        b = api("POST", "/api/v1/chat/query", url, pw,
                json={"limit": 1000, "offset": 1000}, timeout=40).json().get("data", [])
    except Exception as e:
        record(SKIP, "pagination beyond 1000", str(e)[:120])
        return
    if len(a) == 1000 and b:
        record(PASS, "pagination beyond 1000",
               f"page1={len(a)}, page2={len(b)} -- paginate or you WILL miss chats")
    elif len(a) < 1000:
        record(PASS, "pagination not needed", f"{len(a)} chats total")
    else:
        record(SKIP, "pagination beyond 1000", "exactly 1000 chats, page2 empty")


def check_ambiguity_guard(script_dir, url, pw):
    section("Safety guards")
    # These tests must NEVER reach the send endpoint. The old version invoked
    # `bb.py send` and inspected the result afterwards -- but a selector that
    # resolves to exactly ONE chat sends the message before any assertion can
    # run. On a sparse Messages database "+1" can resolve uniquely, so the
    # advertised read-only suite could text a real person. Resolution is now
    # tested through `find`, which never sends.
    env = dict(os.environ, BLUEBUBBLES_SERVER_URL=url, BLUEBUBBLES_PASSWORD=pw)
    bb = script_dir / "bb.py"
    if not bb.exists():
        record(SKIP, "ambiguity guard", "bb.py not found")
        return

    # 1. A broad selector must match many chats. `find` is read-only, so this
    #    establishes ambiguity without risking delivery.
    r = subprocess.run(
        [sys.executable, str(bb), "find", "--query", "+1"],
        capture_output=True, text=True, env=env, timeout=120)
    matches = [l for l in r.stdout.splitlines() if l.strip()]
    if len(matches) > 1:
        record(PASS, "broad selector is genuinely ambiguous",
               f"{len(matches)} chats match '+1'")
    else:
        record(SKIP, "broad selector is genuinely ambiguous",
               f"only {len(matches)} match on this host; guard untestable here")
        return

    # 2. The guard itself, tested WITHOUT sending: resolve_chat must raise on
    #    an ambiguous selector. Import bb.py and call it directly so no send
    #    code path can execute even if the guard is broken.
    probe = (
        "import sys, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('bb', {str(bb)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "url, pw = m.load_config()\n"
        "try:\n"
        "    m.resolve_chat('+1', url, pw)\n"
        "    print('RESOLVED_WITHOUT_REFUSING')\n"
        "except SystemExit as e:\n"
        "    print('REFUSED' if 'ambiguous' in str(e).lower() else f'OTHER_EXIT:{e}')\n"
    )
    r = subprocess.run([sys.executable, "-c", probe],
                       capture_output=True, text=True, env=env, timeout=120)
    if "REFUSED" in r.stdout:
        record(PASS, "ambiguous recipient refused",
               "resolve_chat raised instead of guessing (no send attempted)")
    elif "RESOLVED_WITHOUT_REFUSING" in r.stdout:
        record(FAIL, "ambiguous recipient refused",
               "DANGER: resolve_chat picked a single chat from an ambiguous selector")
    else:
        record(SKIP, "ambiguous recipient refused",
               (r.stdout + r.stderr).strip()[:150])

    # 3. A well-formed GUID for a chat that does not exist must not resolve.
    #    Read-only. NOTE: bb.api() returns PARSED JSON, not a response object,
    #    so checking `.status_code` here would always be falsy and the test
    #    would pass unconditionally. Assert on the payload instead: a real
    #    lookup returns a non-empty data array, a bogus GUID does not.
    probe_missing = (
        "import sys, importlib.util, urllib.parse\n"
        f"spec = importlib.util.spec_from_file_location('bb', {str(bb)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "url, pw = m.load_config()\n"
        "g = 'any;-;+19999999999'\n"
        "enc = urllib.parse.quote(g, safe='')\n"
        "try:\n"
        "    d = m.api('GET', f'/api/v1/chat/{enc}/message?limit=1', url, pw)\n"
        "    rows = d.get('data') if isinstance(d, dict) else None\n"
        "    print('ACCEPTED_WITH_DATA' if rows else 'NO_DATA')\n"
        "except SystemExit as e:\n"
        "    print(f'SERVER_REJECTED:{e}')\n"
    )
    r = subprocess.run([sys.executable, "-c", probe_missing],
                       capture_output=True, text=True, env=env, timeout=120)
    if "SERVER_REJECTED" in r.stdout or "NO_DATA" in r.stdout:
        record(PASS, "nonexistent chat GUID returns nothing",
               "a bogus GUID cannot silently resolve to a real thread")
    elif "ACCEPTED_WITH_DATA" in r.stdout:
        record(FAIL, "nonexistent chat GUID returns nothing",
               "server returned messages for a GUID that should not exist")
    else:
        record(SKIP, "nonexistent chat GUID returns nothing",
               (r.stdout + r.stderr).strip()[:150])


def check_exposure(url, pw):
    section("Security -- network exposure")
    if not CONFIG_DB.exists():
        record(SKIP, "tunnel disabled", "config.db not found")
        return
    try:
        out = subprocess.run(
            ["sqlite3", str(CONFIG_DB),
             "select name||'='||value from config "
             "where name in ('proxy_service','server_address');"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        record(SKIP, "tunnel disabled", str(e)[:120])
        return

    cfg = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
    addr = cfg.get("server_address", "")
    proxy = cfg.get("proxy_service", "")

    if addr.startswith(("http://localhost", "http://127.0.0.1")):
        record(PASS, "server_address is loopback", f"{proxy} -> {addr}")
    else:
        record(FAIL, "server is publicly addressed", f"{proxy} -> {addr}")

    # Config alone is not proof. An orphaned cloudflared survives a restart
    # and keeps serving the old URL -- seen live a real incident.
    r = subprocess.run(
        ["pgrep", "-f", "BlueBubbles.app.*cloudflared"],
        capture_output=True, text=True)
    if r.returncode != 0:
        record(PASS, "no BlueBubbles tunnel process running")
    else:
        record(FAIL, "tunnel process STILL RUNNING",
               f"pid {r.stdout.split()[0]} -- config changed but process survived")

    if addr.startswith("https://") and "trycloudflare" in addr:
        try:
            # NEVER send the real password to a public URL: it would land in
            # Cloudflare/proxy access logs, and this check does not need it.
            # Any HTTP response at all proves the tunnel is still serving.
            resp = requests.get(f"{addr}/api/v1/ping", timeout=25)
            record(FAIL, "public URL unreachable",
                   f"STILL LIVE: HTTP {resp.status_code} from the internet")
        except Exception:
            record(PASS, "public URL unreachable", "tunnel is dead")


def check_hermes_wiring():
    section("Hermes integration")
    if not ENV_PATH.exists():
        record(FAIL, "BLUEBUBBLES_* in .env", f"{ENV_PATH} missing")
        return
    txt = ENV_PATH.read_text()
    need = ["BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_PASSWORD",
            "BLUEBUBBLES_WEBHOOK_PORT"]
    missing = [k for k in need if f"{k}=" not in txt]
    if missing:
        record(FAIL, "BLUEBUBBLES_* in .env", f"missing: {', '.join(missing)}")
    else:
        record(PASS, "BLUEBUBBLES_* in .env", "adapter will load on gateway start")

    mode = oct(ENV_PATH.stat().st_mode)[-3:]
    if mode == "600":
        record(PASS, ".env permissions", "600")
    else:
        record(FAIL, ".env permissions", f"{mode}, expected 600 (holds a password)")


def check_send(url, pw, to, from_name, script_dir):
    section("Send path (live -- texts a real person)")
    # Require an exact GUID. A fuzzy selector could resolve to an unintended
    # unique match, and the read-back below assumes `to` IS the resolved GUID.
    if ";-;" not in to and ";+;" not in to:
        record(FAIL, "--send-to must be an exact chat GUID",
               f"got {to!r}; run: bb.py find --query '<name or number>'")
        return
    bb = script_dir / "bb.py"
    env = dict(os.environ, BLUEBUBBLES_SERVER_URL=url, BLUEBUBBLES_PASSWORD=pw)
    stamp = time.strftime("%H:%M:%S")
    text = (f"[{from_name} / automated test {stamp}] "
            f"Testing the new iMessage bridge. No reply needed.")

    print(f"  sending to {to}")
    r = subprocess.run(
        [sys.executable, str(bb), "send", "--chat", to, "--text", text],
        capture_output=True, text=True, env=env, timeout=300)
    out = (r.stdout + r.stderr)

    # Order matters: "CONFIRMED" is a substring of "UNCONFIRMED", so the
    # unknown case must be tested FIRST or a failed send reads as a success.
    if "DO NOT RETRY" in out or "UNKNOWN" in out:
        record(FAIL, "message send unresolved",
               "may still be in flight -- check Messages.app, do NOT retry")
        return
    elif "CONFIRMED delivered" in out or "sent to" in out:
        record(PASS, "message sent", text[:70] + "...")
    else:
        record(FAIL, "message send", out.strip()[:200])
        return

    # Independent confirmation: read it back from the server, not from the
    # sender's own success claim.
    time.sleep(8)
    guid = to
    enc = urllib.parse.quote(guid, safe="")
    try:
        msgs = api("GET", f"/api/v1/chat/{enc}/message?limit=5", url, pw,
                   timeout=30).json().get("data", [])
        if any(m.get("isFromMe") and text == (m.get("text") or "") for m in msgs):
            record(PASS, "sent message confirmed in thread",
                   "verified by reading the server back")
        else:
            record(FAIL, "sent message not found in thread",
                   "send reported success but message is absent")
    except Exception as e:
        record(SKIP, "read-back confirmation", str(e)[:150])


# ----------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="BlueBubbles bridge test suite")
    p.add_argument("--send-to", help="chat GUID to send a live test message to")
    p.add_argument("--from-name", default="Agent",
                   help="who the test message says it is from")
    p.add_argument("--json", action="store_true", help="emit a JSON summary")
    args = p.parse_args()

    script_dir = Path(__file__).resolve().parent
    print("\033[1mBlueBubbles bridge test suite\033[0m")

    url, pw = load_config()
    if not check_process():
        print("\nServer not running -- cannot continue.")
        sys.exit(2)
    if not check_auth(url, pw):
        print("\nCannot authenticate -- cannot continue.")
        sys.exit(2)

    check_server_info(url, pw)
    check_disk_access(url, pw)
    check_reads(url, pw)
    check_pagination(url, pw)
    check_ambiguity_guard(script_dir, url, pw)
    check_exposure(url, pw)
    check_hermes_wiring()

    if args.send_to:
        check_send(url, pw, args.send_to, args.from_name, script_dir)
    else:
        section("Send path")
        record(SKIP, "live send",
               "pass --send-to '<guid>' to test sending (messages a real person)")

    npass = sum(1 for s, _, _ in results if s == PASS)
    nfail = sum(1 for s, _, _ in results if s == FAIL)
    nskip = sum(1 for s, _, _ in results if s == SKIP)

    print(f"\n\033[1m== Summary ==\033[0m")
    print(f"  {npass} passed, {nfail} failed, {nskip} skipped")
    if nfail:
        print("\n  Failures:")
        for s, n, d in results:
            if s == FAIL:
                print(f"    - {n}: {d}")

    if args.json:
        print(json.dumps({"passed": npass, "failed": nfail, "skipped": nskip,
                          "checks": [{"status": s, "name": n, "detail": d}
                                     for s, n, d in results]}))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
