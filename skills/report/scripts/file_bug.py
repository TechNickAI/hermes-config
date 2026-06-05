#!/usr/bin/env python3
"""file_bug.py — file a fleet bug report onto the triage board and wire the closed loop.

Two delivery paths, auto-selected:

  LOCAL  — when this process is the board-owning profile (the one whose gateway
           runs the kanban dispatcher/notifier, identified by ``BUG_BOARD_OWNER``),
           call the ``hermes kanban`` CLI directly. The card lands in the triage
           column and the reporter's chat is subscribed for the done/blocked ping.

  REMOTE — when run on any other fleet member, POST an HMAC-signed payload to the
           board owner's webhook. If the POST fails (owner gateway down, network
           glitch), fall back to writing a dropfile so the report is never lost,
           and exit non-zero so the caller can DM the human the raw report.

The script is intentionally self-contained (stdlib only) and idempotent: the same
session within a short window produces one card, not many.

Usage:
  file_bug.py --title "..." --body-file /path/to/body.md \\
              [--reporter NAME] [--profile NAME] [--owner-profile board-owner] \\
              [--tenant fleet-bugs] [--json]

Session metadata (platform / chat / thread / user) is read from the environment
the gateway injects (HERMES_SESSION_*). Override with flags for testing.

Exit codes:
  0  card created (or deduped) and closed loop wired
  3  remote POST failed but dropfile written — caller should DM the human
  1  hard failure (bad args, kanban CLI missing, etc.)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as _hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _env(*names: str, default: str = "") -> str:
    """First non-empty env var among names."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def _session_meta() -> dict:
    """Best-effort session identity from the gateway-injected env."""
    return {
        "platform": _env("HERMES_SESSION_PLATFORM", "HERMES_PLATFORM", default="cli"),
        "chat_id": _env("HERMES_SESSION_CHAT_ID"),
        "thread_id": _env("HERMES_SESSION_THREAD_ID"),
        "user_id": _env("HERMES_SESSION_USER_ID"),
        "user_name": _env("HERMES_SESSION_USER_NAME"),
        "session_id": _env("HERMES_SESSION_ID"),
        "session_key": _env("HERMES_SESSION_KEY"),
    }


def _active_profile() -> str:
    # HERMES_PROFILE is set when running under a named profile; the default
    # profile leaves it unset, in which case the HERMES_HOME path tail is the tell.
    p = _env("HERMES_PROFILE")
    if p:
        return p
    home = os.environ.get("HERMES_HOME", "")
    if "/profiles/" in home:
        return home.rstrip("/").split("/profiles/")[-1].split("/")[0]
    return "default"


def _idempotency_key(meta: dict, title: str = "") -> str:
    # One card per (reporter + title-hash) per ~2-minute bucket.
    # Including the title means two different reports from the same session within
    # the window still produce distinct cards.  A per-process nonce is used when no
    # session identifier is present so unrelated CLI callers never share a key.
    bucket = int(time.time()) // 120
    reporter_id = (
        meta.get("session_id")
        or meta.get("session_key")
        or meta.get("chat_id")
        or meta.get("user_id")
        # Last resort: per-process nonce so concurrent CLI callers don't collide.
        or str(os.getpid())
    )
    title_slug = hashlib.sha256(title.encode()).hexdigest()[:8] if title else "notitle"
    basis = f"{reporter_id}:{title_slug}:{bucket}"
    return "bug-" + hashlib.sha256(basis.encode()).hexdigest()[:16]


def _run(cmd: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _dropfile(payload: dict, reason: str) -> Path:
    """Persist a report that couldn't be delivered so nothing is lost."""
    d = Path(os.path.expanduser("~/.hermes")) / "bug-report-dropbox"
    d.mkdir(parents=True, exist_ok=True)
    fname = f"bug-{int(time.time())}-{payload['idempotency_key'][-8:]}.json"
    f = d / fname
    f.write_text(json.dumps({"reason": reason, "payload": payload}, indent=2))
    return f


# --------------------------------------------------------------------------- #
# LOCAL path — board owner calls the kanban CLI directly.
# --------------------------------------------------------------------------- #
def file_local(args: argparse.Namespace, meta: dict, body: str, idem: str) -> dict:
    create_cmd = [
        "hermes", "kanban", "create", args.title,
        "--triage",
        "--tenant", args.tenant,
        "--created-by", args.reporter or meta.get("user_name") or "fleet-user",
        "--idempotency-key", idem,
        "--body", body,
        "--json",
    ]
    rc, out, err = _run(create_cmd)
    if rc != 0:
        raise RuntimeError(f"kanban create failed (rc={rc}): {err or out}")
    task = json.loads(out)
    task_id: str = task["id"]

    # Closed loop: subscribe the reporter's chat so `kanban complete` pings them.
    # Only wired when we have a real gateway chat (platform != cli) with a chat_id.
    subscribed = False
    platform = meta.get("platform", "")
    chat_id = meta.get("chat_id", "")
    if platform and platform != "cli" and chat_id:
        sub_cmd = [
            "hermes", "kanban", "notify-subscribe", task_id,
            "--platform", platform,
            "--chat-id", chat_id,
        ]
        if meta.get("thread_id"):
            sub_cmd += ["--thread-id", meta["thread_id"]]
        if meta.get("user_id"):
            sub_cmd += ["--user-id", meta["user_id"]]
        src, _sout, _serr = _run(sub_cmd)
        subscribed = src == 0

    return {
        "ok": True,
        "path": "local",
        "task_id": task_id,
        "status": task.get("status"),
        "subscribed": subscribed,
    }


# --------------------------------------------------------------------------- #
# REMOTE path — non-owner fleet member POSTs an HMAC-signed payload.
# --------------------------------------------------------------------------- #
def file_remote(args: argparse.Namespace, meta: dict, body: str, idem: str) -> dict:
    url = _env("BUG_WEBHOOK_URL")
    secret = _env("BUG_WEBHOOK_SECRET")
    payload = {
        "title": args.title,
        "body": body,
        "reporter": args.reporter or meta.get("user_name") or "fleet-user",
        "profile": args.profile or _active_profile(),
        "tenant": args.tenant,
        "idempotency_key": idem,
        "platform": meta.get("platform"),
        "chat_id": meta.get("chat_id"),
        "thread_id": meta.get("thread_id"),
        "user_id": meta.get("user_id"),
        "session_id": meta.get("session_id"),
        "timestamp": int(time.time()),
    }
    if not url or not secret:
        f = _dropfile(payload, "no webhook url/secret configured")
        return {
            "ok": False, "path": "remote", "dropfile": str(f),
            "error": "BUG_WEBHOOK_URL / BUG_WEBHOOK_SECRET not set",
        }

    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = _hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url, data=raw,
        headers={
            "content-type": "application/json",
            "x-hermes-signature": f"sha256={sig}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode() or "{}")
        task_id = resp.get("task_id") or resp.get("id")
        if not task_id:
            # Server returned 2xx but no task id — treat as a delivery failure.
            f = _dropfile(payload, f"remote returned 2xx but no task_id: {resp!r:.200}")
            return {
                "ok": False, "path": "remote", "dropfile": str(f),
                "error": f"server response missing task_id: {resp!r:.100}",
            }
        return {
            "ok": True, "path": "remote",
            "task_id": task_id,
            "status": resp.get("status"),
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        f = _dropfile(payload, f"POST failed: {type(e).__name__}: {e}")
        return {"ok": False, "path": "remote", "dropfile": str(f), "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description="File a fleet bug report.")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body-file", help="Path to markdown file with the report body.")
    ap.add_argument("--body", help="Inline body (alternative to --body-file).")
    ap.add_argument("--reporter", default="")
    ap.add_argument("--profile", default="")
    ap.add_argument(
        "--owner-profile",
        default=os.environ.get("BUG_BOARD_OWNER", "board-owner"),
        help="Profile that owns the triage board (local short-circuit target). "
             "Set BUG_BOARD_OWNER env var or pass --owner-profile to override.",
    )
    ap.add_argument("--tenant", default="fleet-bugs")
    ap.add_argument(
        "--force-remote", action="store_true",
        help="Force the remote POST path even on the owner (for testing).",
    )
    ap.add_argument(
        "--force-local", action="store_true",
        help="Force the local kanban path (single-host installs / testing).",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    body = ""
    if args.body_file and Path(args.body_file).exists():
        body = Path(args.body_file).read_text()
    elif args.body:
        body = args.body
    if not body.strip():
        body = (
            "(no additional context captured)\n\n"
            f"Reported: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    meta = _session_meta()
    idem = _idempotency_key(meta, args.title)
    active = _active_profile()

    # Routing is by *capability*, not by name-matching, to avoid the footgun
    # where an unconfigured owner silently misroutes to the remote path and
    # breaks. Rules, in priority order:
    #   1. --force-remote / --force-local always win (testing + explicit control).
    #   2. If this profile is explicitly named as the board owner, go local.
    #   3. If a webhook URL is configured, this is a satellite -> remote.
    #      (The board owner has direct kanban access and no reason to configure
    #      a webhook pointing at itself.)
    #   4. Otherwise (no webhook configured), assume local: either this profile
    #      owns the board or it's a standalone single-host install. Filing
    #      directly to the local board is the safe default — worst case the
    #      report lands on this host's board instead of disappearing.
    owner_named = bool(args.owner_profile) and args.owner_profile != "board-owner"
    has_webhook = bool(_env("BUG_WEBHOOK_URL"))

    if args.force_remote:
        is_owner = False
    elif args.force_local:
        is_owner = True
    elif owner_named:
        is_owner = active == args.owner_profile
    elif has_webhook:
        is_owner = False
    else:
        is_owner = True

    try:
        result = (
            file_local(args, meta, body, idem)
            if is_owner
            else file_remote(args, meta, body, idem)
        )
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "path": "local" if is_owner else "remote", "error": str(e)}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("ok"):
            tid = result.get("task_id", "?")
            loop = (
                " You'll get a ping here when it's resolved." if result.get("subscribed") else ""
            )
            print(f"Filed as {tid}.{loop}")
        else:
            drop = result.get("dropfile")
            print(
                f"Could not file bug: {result.get('error')}."
                + (f" Saved to {drop} — DM the human the raw report." if drop else "")
            )

    if result.get("ok"):
        return 0
    return 3 if result.get("dropfile") else 1


if __name__ == "__main__":
    sys.exit(main())
