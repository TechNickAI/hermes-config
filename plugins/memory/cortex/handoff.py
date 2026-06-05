"""Handoff extraction for the cortex pre-compress hook.

When Hermes compresses a long conversation it drops old messages, keeping a
summary plus a tail. For long-running task threads (especially Telegram topics)
that summary can lose the concrete spine of the work: what we're trying to do,
what's been decided, which files/URLs are in play, and what the next move is.

This module builds a compact, deterministic *handoff digest* from the live
message list — no LLM call, fast, and fail-safe. The cortex provider writes the
digest to a topic-keyed KB page (`handoff/handoff-<topic>.md`) so it is:

  • FTS5-indexed → re-pulled by the normal prefetch hook on the next turn, and
  • durable on disk → survives the compaction that triggered it.

The digest is organized under stable headings (GOAL / STATE / DECISIONS /
OPEN LOOPS / ARTIFACTS / NEXT MOVE). Headings are always emitted so the shape is
predictable; sections we can't reliably derive heuristically are left with a
short placeholder rather than guessed-at content.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Caps keep the digest small — it rides inside the compression window and a
# prefetch slot, so it must stay cheap.
_MAX_GOAL_CHARS = 600
_MAX_TURN_CHARS = 500
_MAX_RECENT_TURNS = 6
_MAX_ARTIFACTS = 20
_MAX_BODY_CHARS = 6000

# Artifact patterns: absolute/relative file paths, ~ paths, and URLs.
_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
# A path is one or more `segment/` parts followed by `name.ext`. This catches
# absolute (`/a/b.py`), home (`~/src/x.md`), relative (`./a/b.py`), and bare
# relative (`plugins/memory/cortex/__init__.py`) forms. The lookbehind keeps us
# from starting a match inside a URL's path (those are captured by _URL_RE).
_PATH_RE = re.compile(
    r"(?<![\w@:/])"           # not mid-word and not inside a URL path
    r"(?:~|\.{1,2})?/?"       # optional ~ / . / .. then optional slash
    r"(?:[\w.\-]+/)+"         # one or more `dir/` segments
    r"[\w.\-]+"               # file stem
    r"\.[A-Za-z0-9_]{1,8}"   # extension
)
# Decision/next-move signal phrases (lowercased match).
_DECISION_HINTS = (
    "decided", "decision:", "we'll go with", "going with", "let's use",
    "chose ", "chosen", "the plan is", "approach:", "agreed",
)
_NEXT_HINTS = (
    "next step", "next move", "next:", "todo", "to do", "i'll ", "i will ",
    "then i", "after that", "remaining", "still need", "want me to",
)


def _content_str(message: Dict[str, Any]) -> str:
    """Coerce a message's content into a plain string.

    OpenAI-format content can be a string or a list of content parts; tool
    results and reasoning are ignored here — we only want human-readable text.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def extract_artifacts(messages: List[Dict[str, Any]]) -> List[str]:
    """Pull file paths and URLs mentioned anywhere in the conversation."""
    found: List[str] = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        text = _content_str(m)
        if not text:
            continue
        found.extend(_URL_RE.findall(text))
        found.extend(_PATH_RE.findall(text))
    # Strip common trailing punctuation that regex may capture.
    cleaned = [a.rstrip(".,;:)]}\"'") for a in found]
    cleaned = [a for a in cleaned if len(a) > 3]
    return _dedupe_keep_order(cleaned)[:_MAX_ARTIFACTS]


def _last_user_goal(messages: List[Dict[str, Any]]) -> str:
    """The most recent substantive user message = the current goal/ask."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        text = _content_str(m).strip()
        if len(text) >= 12:  # skip "ok", "yes", "👍", etc.
            return _clip(text, _MAX_GOAL_CHARS)
    # Fall back to the first user message if nothing substantive at the tail.
    for m in messages:
        if m.get("role") == "user":
            text = _content_str(m).strip()
            if text:
                return _clip(text, _MAX_GOAL_CHARS)
    return ""


def _recent_exchange(messages: List[Dict[str, Any]]) -> List[str]:
    """Last few user/assistant turns, trimmed, oldest-first."""
    turns: List[str] = []
    for m in reversed(messages):
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_str(m).strip()
        if not text:
            continue
        label = "You" if role == "user" else "Me"
        turns.append(f"**{label}:** {_clip(text, _MAX_TURN_CHARS)}")
        if len(turns) >= _MAX_RECENT_TURNS:
            break
    return list(reversed(turns))


def _hint_lines(messages: List[Dict[str, Any]], hints: tuple[str, ...]) -> List[str]:
    """Sentences from recent messages that match any of the hint phrases."""
    out: List[str] = []
    # Look only at the recent tail — older hints are stale.
    for m in messages[-30:]:
        if m.get("role") not in ("user", "assistant"):
            continue
        text = _content_str(m)
        if not text:
            continue
        for raw in re.split(r"(?<=[.!?\n])\s+", text):
            s = raw.strip()
            if not s:
                continue
            low = s.lower()
            if any(h in low for h in hints):
                out.append(_clip(s, 220))
    return _dedupe_keep_order(out)[:6]


def build_handoff(
    messages: List[Dict[str, Any]],
    *,
    topic_label: str = "",
    now_str: str = "",
) -> str:
    """Build the markdown handoff body. Returns ``""`` if nothing useful."""
    if not messages:
        return ""

    goal = _last_user_goal(messages)
    exchange = _recent_exchange(messages)
    if not goal and not exchange:
        return ""

    artifacts = extract_artifacts(messages)
    decisions = _hint_lines(messages, _DECISION_HINTS)
    next_moves = _hint_lines(messages, _NEXT_HINTS)

    lines: List[str] = []
    if topic_label:
        lines.append(f"_Topic: {topic_label}_")
    if now_str:
        lines.append(f"_Captured at compaction: {now_str}_")
    if lines:
        lines.append("")

    lines.append("## GOAL")
    lines.append(goal or "_(not clearly stated in recent turns)_")
    lines.append("")

    lines.append("## STATE — recent exchange")
    if exchange:
        lines.extend(exchange)
    else:
        lines.append("_(no recent user/assistant turns captured)_")
    lines.append("")

    lines.append("## DECISIONS")
    if decisions:
        lines.extend(f"- {d}" for d in decisions)
    else:
        lines.append("_(none detected heuristically — see STATE)_")
    lines.append("")

    lines.append("## OPEN LOOPS / NEXT MOVE")
    if next_moves:
        lines.extend(f"- {n}" for n in next_moves)
    else:
        lines.append("_(none detected heuristically — see STATE)_")
    lines.append("")

    lines.append("## ARTIFACTS")
    if artifacts:
        lines.extend(f"- `{a}`" for a in artifacts)
    else:
        lines.append("_(no files or URLs referenced)_")

    body = "\n".join(lines).strip()
    return _clip(body, _MAX_BODY_CHARS)


def _encode_id(value: str) -> str:
    """Sanitize one id component into a slug-safe token, sign-preserving.

    Negative gateway chat IDs (Telegram groups are negative) must not collide
    with their positive counterpart: a naive ``strip("-")`` would map both
    ``-1002`` and ``1002`` to ``1002``. We detect a leading minus and prefix the
    cleaned body with ``n`` instead of dropping the sign.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    neg = raw.startswith("-")
    body = re.sub(r"[^A-Za-z0-9_]+", "-", raw).strip("-")
    if not body:
        return ""
    return f"n{body}" if neg else body


def handoff_slug(chat_id: str = "", thread_id: str = "", session_id: str = "") -> str:
    """Deterministic, topic-stable slug so re-compactions overwrite in place.

    Prefer chat+thread (a Telegram topic / Discord thread is the durable unit of
    work). Fall back to session_id, then a constant. Each component is
    sign-encoded so negative chat IDs stay distinct from positive ones.
    """
    parts = [t for t in (_encode_id(chat_id), _encode_id(thread_id)) if t]
    if parts:
        key = "-".join(parts)
    else:
        key = _encode_id(session_id) or "default"
    return f"handoff-{key}"
