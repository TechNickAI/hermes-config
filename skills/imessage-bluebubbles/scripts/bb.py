#!/usr/bin/env python3
"""bb.py -- iMessage interactions via the BlueBubbles REST API.

The agent-facing replacement for the `imsg` CLI. Talks HTTP to a local
BlueBubbles server instead of reading Apple's chat.db directly, so it is
immune to the Full-Disk-Access revocations and sandboxd hangs that break
direct-database tools after macOS/Python upgrades.

Reads BLUEBUBBLES_SERVER_URL and BLUEBUBBLES_PASSWORD from the environment
or from a Hermes .env file.

Usage:
  bb.py health
  bb.py chats [--limit N]
  bb.py history --chat <guid|search> [--limit N]
  bb.py send --chat <guid|search> --text "message"
  bb.py find --query "name or number"

Output is plain text, one record per line -- readable by a human and by an
LLM without a JSON parse step.
"""

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests not installed: pip install requests")

DEFAULT_ENV = Path.home() / ".hermes" / ".env"


def load_config():
    """Env vars win; fall back to the Hermes .env file."""
    url = os.getenv("BLUEBUBBLES_SERVER_URL")
    pw = os.getenv("BLUEBUBBLES_PASSWORD")

    if (not url or not pw) and DEFAULT_ENV.exists():
        for line in DEFAULT_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k == "BLUEBUBBLES_SERVER_URL" and not url:
                url = v.strip()
            elif k == "BLUEBUBBLES_PASSWORD" and not pw:
                pw = v.strip()

    if not url or not pw:
        sys.exit(
            "BlueBubbles not configured.\n"
            "  Set BLUEBUBBLES_SERVER_URL and BLUEBUBBLES_PASSWORD, or add them\n"
            f"  to {DEFAULT_ENV}. Run setup-bluebubbles.sh to configure."
        )
    return url.rstrip("/"), pw


def api(method, path, url, pw, **kwargs):
    """One call site for every request so auth and errors stay consistent."""
    sep = "&" if "?" in path else "?"
    full = f"{url}{path}{sep}password={urllib.parse.quote(pw, safe='')}"
    kwargs.setdefault("timeout", 20)
    try:
        r = requests.request(method, full, **kwargs)
    except requests.exceptions.ConnectionError:
        sys.exit(
            f"Cannot reach BlueBubbles at {url}\n"
            "  Is the BlueBubbles app running? Open it and retry."
        )
    except requests.exceptions.Timeout:
        sys.exit(f"BlueBubbles timed out at {url} -- the server may be wedged.")

    if r.status_code == 401:
        sys.exit("Authentication failed -- BLUEBUBBLES_PASSWORD is wrong.")
    if r.status_code >= 400:
        sys.exit(f"BlueBubbles returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        sys.exit(f"Non-JSON response from BlueBubbles: {r.text[:200]}")


def resolve_chat(selector, url, pw):
    """Accept a raw GUID or a fuzzy name/number and return one GUID.

    Exits with the candidate list when a search is ambiguous, rather than
    silently guessing -- sending a message to the wrong person is not a
    recoverable error.
    """
    # A GUID is any selector carrying the service-prefix separator ';-;'
    # (1:1) or ';+;' (group). Services seen in the wild include iMessage,
    # SMS, and 'any' -- matching on service names misses 'any;-;+1555...'
    # and silently falls through to fuzzy search, which then reports
    # "no chat matches" for a GUID that is perfectly valid.
    if ";-;" in selector or ";+;" in selector:
        return selector

    data = api("POST", "/api/v1/chat/query", url, pw,
               json={"limit": 1000, "offset": 0, "with": ["participants"]})
    chats = data.get("data", []) or []

    needle = selector.lower()
    matches = []
    for c in chats:
        name = (c.get("displayName") or "").lower()
        parts = " ".join(
            (p.get("address") or "") for p in (c.get("participants") or [])
        ).lower()
        if needle in name or needle in parts:
            matches.append(c)

    if not matches:
        sys.exit(f"No chat matches '{selector}'. Try: bb.py chats")
    if len(matches) > 1:
        lines = [f"'{selector}' is ambiguous -- {len(matches)} matches:"]
        for c in matches[:10]:
            lines.append(f"  {describe_chat(c)}")
        lines.append("Re-run with the exact GUID.")
        sys.exit("\n".join(lines))
    return matches[0].get("guid")


def describe_chat(c):
    name = c.get("displayName") or ""
    parts = [p.get("address", "") for p in (c.get("participants") or [])]
    who = name or ", ".join(parts) or "(unknown)"
    return f"{who}  [{c.get('guid', '')}]"


def cmd_health(args, url, pw):
    api("GET", "/api/v1/ping", url, pw)
    info = api("GET", "/api/v1/server/info", url, pw).get("data", {})
    # chat/query is the real Full Disk Access test -- ping passes without it.
    chats = api("POST", "/api/v1/chat/query", url, pw,
                json={"limit": 1, "offset": 0}).get("data", [])
    print(f"server:      reachable at {url}")
    print(f"version:     {info.get('server_version', 'unknown')}")
    print(f"macos:       {info.get('os_version', 'unknown')}")
    print(f"private_api: {info.get('private_api', 'unknown')}")
    print(f"chat access: {'ok' if chats else 'NO DATA -- check Full Disk Access'}")


def cmd_chats(args, url, pw):
    data = api("POST", "/api/v1/chat/query", url, pw,
               json={"limit": args.limit, "offset": 0,
                     "with": ["participants", "lastMessage"]})
    chats = data.get("data", []) or []
    if not chats:
        print("No chats found. If this is unexpected, check Full Disk Access.")
        return
    for c in chats:
        print(describe_chat(c))


def cmd_find(args, url, pw):
    data = api("POST", "/api/v1/chat/query", url, pw,
               json={"limit": 1000, "offset": 0, "with": ["participants"]})
    needle = args.query.lower()
    hits = 0
    for c in data.get("data", []) or []:
        name = (c.get("displayName") or "").lower()
        parts = " ".join(
            (p.get("address") or "") for p in (c.get("participants") or [])
        ).lower()
        if needle in name or needle in parts:
            print(describe_chat(c))
            hits += 1
    if not hits:
        print(f"No chat matches '{args.query}'.")


def cmd_history(args, url, pw):
    guid = resolve_chat(args.chat, url, pw)
    enc = urllib.parse.quote(guid, safe="")
    data = api("GET",
               f"/api/v1/chat/{enc}/message?limit={args.limit}&with=handle",
               url, pw)
    msgs = data.get("data", []) or []
    if not msgs:
        print("(no messages)")
        return
    for m in reversed(msgs):  # API returns newest first; read oldest->newest
        who = "me" if m.get("isFromMe") else (
            (m.get("handle") or {}).get("address") or "them"
        )
        # BlueBubbles decodes attributedBody for us, so text is populated
        # even on modern macOS where chat.db's text column is NULL.
        text = (m.get("text") or "").replace("\n", " ").strip()
        if not text:
            text = "[attachment or reaction]"
        print(f"{who}: {text}")


def cmd_send(args, url, pw):
    guid = resolve_chat(args.chat, url, pw)
    import uuid
    payload = {
        "chatGuid": guid,
        "message": args.text,
        "tempGuid": uuid.uuid4().hex,
        "method": "apple-script",
    }
    # AppleScript sends routinely take longer than the default timeout: the
    # request blocks while Messages.app does the work, but the send still
    # COMPLETES. A timeout here means UNKNOWN, never failed.
    try:
        api("POST", "/api/v1/message/text", url, pw, json=payload, timeout=120)
        print(f"sent to {guid}: {args.text}")
        return
    except SystemExit as exc:
        # api() exits on timeout. Do not propagate that as failure and do NOT
        # retry -- retrying an in-flight send double-messages a real person.
        if "timed out" not in str(exc):
            raise

    # Resolve the unknown by reading the thread back, after a delay long
    # enough for a slow send to land. This is the ONLY safe way to find out.
    print("send timed out -- verifying whether it landed (do not retry)...")
    import time
    enc = urllib.parse.quote(guid, safe="")
    for attempt in range(3):
        time.sleep(10)
        data = api("GET", f"/api/v1/chat/{enc}/message?limit=5", url, pw)
        for m in data.get("data", []) or []:
            if m.get("isFromMe") and (m.get("text") or "") == args.text:
                print(f"CONFIRMED sent to {guid}: {args.text}")
                return
    sys.exit(
        f"UNCONFIRMED: no matching message found in {guid} after 30s.\n"
        "  The send may still be in flight. Check Messages.app before\n"
        "  retrying -- retrying may deliver the message twice."
    )


def main():
    p = argparse.ArgumentParser(description="iMessage via BlueBubbles")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="check server, auth, and disk access")

    c = sub.add_parser("chats", help="list recent chats")
    c.add_argument("--limit", type=int, default=25)

    f = sub.add_parser("find", help="search chats by name or number")
    f.add_argument("--query", required=True)

    h = sub.add_parser("history", help="read a conversation")
    h.add_argument("--chat", required=True)
    h.add_argument("--limit", type=int, default=25)

    s = sub.add_parser("send", help="send a message")
    s.add_argument("--chat", required=True)
    s.add_argument("--text", required=True)

    args = p.parse_args()
    url, pw = load_config()
    {"health": cmd_health, "chats": cmd_chats, "find": cmd_find,
     "history": cmd_history, "send": cmd_send}[args.cmd](args, url, pw)


if __name__ == "__main__":
    main()
