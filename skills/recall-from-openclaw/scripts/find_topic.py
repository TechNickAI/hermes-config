#!/usr/bin/env python3
"""
Find an OpenClaw session transcript that matches a given Telegram thread_id,
emit a compact JSON summary of candidates and a tailed transcript for the agent
to synthesize a context briefing.

OpenClaw session-file naming convention:
  ~/.openclaw[-<instance>]/agents/main/sessions/<uuid>-topic-<thread_id>.jsonl
  ~/.openclaw[-<instance>]/agents/main/sessions/<uuid>.jsonl  (cron / non-topic)

Resolution order for the search root:
  1. --root <abs-path>            (explicit override)
  2. $OPENCLAW_HOME                (env var, if set)
  3. ~/.openclaw-<instance>/agents/main/sessions  (for each candidate instance)
  4. ~/.openclaw/agents/main/sessions             (last-resort default)

Outputs JSON on stdout (always). Exit 0 on found, 1 on not-found, 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TAIL_MAX_CHARS = 50_000  # cap synthesized tail so context stays manageable
# Match live topic transcripts AND archived reset transcripts. OpenClaw's reset flow
# renames the prior session to ``<base>.jsonl.reset.<iso-timestamp>`` where the
# timestamp can contain digits, ``-``, ``T``, ``:``, ``.``, and ``Z`` (e.g.
# ``2026-05-19T20-50-48.890Z``). We deliberately match neither ``.trajectory.jsonl``
# (different format — agent traces, not human dialog) nor ``.jsonl.bak-*`` (stale
# within-session backups).
TOPIC_FILENAME_RE = re.compile(
    r"^(?P<uuid>[0-9a-f-]{36})-topic-(?P<thread>\d+)\.jsonl(?:\.reset\.[\dT:\-.Z]+)?$"
)


@dataclass
class Hit:
    path: Path
    thread_id: str
    uuid: str
    size: int
    mtime: float


def _expand_sessions_dirs(base: Path) -> list[Path]:
    """Given an OpenClaw home dir, return every ``agents/*/sessions`` subdir.

    OpenClaw runs one or more named agents (``main``, ``sterling``, ``claude``, …)
    side by side under ``agents/<name>/sessions``. We need to search all of them
    because a fleet member's Telegram topic could be served by any agent.
    """
    out: list[Path] = []
    agents_dir = base / "agents"
    if agents_dir.is_dir():
        for child in sorted(agents_dir.iterdir()):
            if not child.is_dir():
                continue
            sess = child / "sessions"
            if sess.is_dir():
                out.append(sess)
    # Some older installs put sessions at the home root
    legacy = base / "sessions"
    if legacy.is_dir():
        out.append(legacy)
    return out


def candidate_session_dirs(explicit_root: str | None) -> list[Path]:
    if explicit_root:
        p = Path(explicit_root).expanduser()
        # Reject non-directories up front so downstream globbing can't raise NotADirectoryError
        if not p.is_dir():
            return []
        # Accept either a direct sessions/ dir or an openclaw home dir
        if p.name == "sessions":
            return [p]
        expanded = _expand_sessions_dirs(p)
        return expanded if expanded else [p]

    out: list[Path] = []
    env = os.environ.get("OPENCLAW_HOME")
    if env:
        out.extend(_expand_sessions_dirs(Path(env).expanduser()))

    # Probe ~/.openclaw-<anything> and ~/.openclaw
    home = Path(os.path.expanduser("~"))
    # Real user home (in case HOME has been profile-rewritten)
    try:
        import pwd
        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        real_home = home
    seen_bases = set()
    for base in [home, real_home]:
        if base in seen_bases:
            continue
        seen_bases.add(base)
        for d in sorted(base.glob(".openclaw*")):
            out.extend(_expand_sessions_dirs(d))

    # De-dupe while preserving order
    deduped: list[Path] = []
    seen_paths: set[Path] = set()
    for p in out:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp not in seen_paths:
            seen_paths.add(rp)
            deduped.append(p)
    return deduped


def find_topic_sessions(dirs: Iterable[Path], thread_id: str) -> list[Hit]:
    hits: list[Hit] = []
    for d in dirs:
        # Belt-and-suspenders: skip anything that isn't a real directory so we never
        # raise NotADirectoryError mid-search.
        if not d.is_dir():
            continue
        # ``*-topic-<thread>.jsonl*`` covers both live ``.jsonl`` and archived
        # ``.jsonl.reset.<ts>`` transcripts. Final filter is the regex below.
        for f in d.glob(f"*-topic-{thread_id}.jsonl*"):
            m = TOPIC_FILENAME_RE.match(f.name)
            if not m:
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            hits.append(Hit(path=f, thread_id=m.group("thread"), uuid=m.group("uuid"),
                            size=st.st_size, mtime=st.st_mtime))
    # Newest first
    hits.sort(key=lambda h: -h.mtime)
    return hits


def extract_human_text(content: object) -> str:
    """Return a flat string from OpenClaw's structured message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool_use: {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    parts.append("[tool_result]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)


def tail_transcript(path: Path, max_chars: int) -> dict:
    """Read a session jsonl and return a compact summary + tail of user/assistant messages."""
    user_msgs: list[tuple[str, str]] = []  # (role, text)
    first_user = None
    counts = {"user": 0, "assistant": 0, "tool": 0}

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "message":
                    continue
                msg = obj.get("message") or {}
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = extract_human_text(msg.get("content"))
                if not text.strip():
                    continue
                # Strip OpenClaw's auto-injected metadata blocks ("Sender (untrusted metadata)",
                # "Conversation info (untrusted metadata)", "Conversation context (untrusted metadata)")
                # so the synthesized briefing sees actual conversation content, not envelope noise.
                while True:
                    md_idx = text.find("(untrusted metadata)")
                    if md_idx < 0:
                        break
                    fence_open = text.find("```", md_idx)
                    if fence_open < 0:
                        break
                    fence_close = text.find("```", fence_open + 3)
                    if fence_close < 0:
                        break
                    # Find the start of the label (look backward for the previous newline pair)
                    label_start = text.rfind("\n\n", 0, md_idx)
                    label_start = label_start + 2 if label_start >= 0 else 0
                    # If the metadata block is right at the head, just nuke from there
                    if label_start > md_idx - 200:
                        text = (text[:label_start] + text[fence_close + 3:]).lstrip()
                    else:
                        text = (text[:md_idx] + text[fence_close + 3:]).lstrip()
                # Re-check after stripping — a message composed entirely of metadata
                # leaves an empty string here and shouldn't count toward msg totals
                # or land as a blank first_user/tail entry.
                if not text.strip():
                    continue
                counts[role] = counts.get(role, 0) + 1
                if first_user is None and role == "user":
                    first_user = text[:500]
                user_msgs.append((role, text))
    except OSError as exc:
        return {"error": f"read error: {exc}"}

    # Take tail under cap, prefer recent messages. Hard cap so a single huge message
    # can't blow past max_chars and flood the agent's context — truncate if needed.
    tail: list[tuple[str, str]] = []
    running = 0
    for role, text in reversed(user_msgs):
        snippet = f"[{role}]\n{text}\n\n"
        if running + len(snippet) > max_chars:
            if tail:
                break
            # First (most-recent) message alone exceeds the cap — truncate to fit
            # rather than emit zero tail messages.
            overhead = len(f"[{role}]\n\n\n") + len(" …[truncated]")
            budget = max(0, max_chars - overhead)
            truncated = text[:budget] + " …[truncated]" if budget > 0 else " …[truncated]"
            tail.append((role, truncated))
            running += len(f"[{role}]\n{truncated}\n\n")
            break
        tail.append((role, text))
        running += len(snippet)
    tail.reverse()

    return {
        "counts": counts,
        "first_user_message": first_user,
        "tail_messages": [{"role": r, "text": t} for r, t in tail],
        "tail_message_count": len(tail),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Find an OpenClaw session by Telegram thread id")
    ap.add_argument("--thread-id", default=os.environ.get("HERMES_SESSION_THREAD_ID", ""),
                    help="Telegram message_thread_id (default: $HERMES_SESSION_THREAD_ID)")
    ap.add_argument("--root", default="",
                    help="Explicit absolute path to an OpenClaw sessions/ dir (skips auto-discovery)")
    ap.add_argument("--list-only", action="store_true",
                    help="List candidate hits but don't read transcripts")
    ap.add_argument("--max-chars", type=int, default=TAIL_MAX_CHARS,
                    help=f"Cap synthesized tail size in characters (default: {TAIL_MAX_CHARS})")
    args = ap.parse_args()

    thread_id = args.thread_id.strip()
    if not thread_id or not thread_id.isdigit():
        print(json.dumps({
            "ok": False,
            "error": "Missing/invalid thread_id. Pass --thread-id or run inside a Telegram-topic session.",
            "hint": "Check $HERMES_SESSION_THREAD_ID -- only forum-supergroup topics have one.",
        }, indent=2))
        return 2

    dirs = candidate_session_dirs(args.root or None)
    if not dirs:
        print(json.dumps({
            "ok": False,
            "thread_id": thread_id,
            "error": "No OpenClaw sessions/ directory found. Set OPENCLAW_HOME or pass --root.",
            "searched": [],
        }, indent=2))
        return 1

    hits = find_topic_sessions(dirs, thread_id)
    if not hits:
        print(json.dumps({
            "ok": False,
            "thread_id": thread_id,
            "error": f"No OpenClaw transcript found for thread_id {thread_id}.",
            "searched": [str(d) for d in dirs],
        }, indent=2))
        return 1

    primary = hits[0]
    result = {
        "ok": True,
        "thread_id": thread_id,
        "searched": [str(d) for d in dirs],
        "candidates": [
            {
                "path": str(h.path),
                "uuid": h.uuid,
                "size_bytes": h.size,
                "mtime_iso": __import__("datetime").datetime.fromtimestamp(h.mtime).isoformat(timespec="seconds"),
            }
            for h in hits
        ],
        "primary": str(primary.path),
    }

    if not args.list_only:
        transcript = tail_transcript(primary.path, args.max_chars)
        result["transcript"] = transcript
        # If reading the primary transcript failed, flip the overall result to
        # not-ok so downstream consumers don't synthesize briefings from missing
        # data. Candidates list still gives the agent something to act on
        # (e.g. retry with --root, or try another candidate).
        if isinstance(transcript, dict) and transcript.get("error"):
            result["ok"] = False
            result["error"] = f"Read failed for primary transcript: {transcript['error']}"
            print(json.dumps(result, indent=2))
            return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
