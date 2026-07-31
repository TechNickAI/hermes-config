#!/usr/bin/env python3
"""Living board: collapse a chat topic into ONE self-updating message.

THE PROBLEM

Agents that run on a schedule append. A pass finds something, posts it, and moves on. After a
day you have a channel holding a day of messages, and reading it means replaying the agent's
whole thought process in order. The measured case that produced this tool: 37 messages and
43,911 characters in a single "brief" channel in 24 hours, of which 15 were raw job status
posts. By the time the human read to the bottom, most items had already been resolved by later
passes. The catch-up cost exceeded the information value, so the channel stopped being read.

Posting less is not the fix, because the useful items are real. The fix is that a notification
channel should hold STATE, not HISTORY.

THE MECHANISM

Chat platforms have no dashboard, but a bot can edit and pin its own messages. So one pinned
message can behave like a dashboard: always current, edited in place, with resolved items
removed rather than struck through. The channel converges on one message that answers "what is
true right now", and history lives where history belongs, in the project's own log.

Requires a bot with permission to send, edit, pin, and delete its own messages in the target
chat. Verified against the Telegram Bot API; the four primitives used are sendMessage,
editMessageText, pinChatMessage and deleteMessage.

CONFIGURATION

Reads ~/.hermes/board.toml (override with BOARD_CONFIG):

    bot_token_env = "TELEGRAM_BOT_TOKEN"   # env var holding the token
    chat_id = -1000000000000               # the group/supergroup
    timezone = "UTC"

    [topics]
    brief = 1234                           # name -> message_thread_id
    needs-me = 5678

For a non-forum chat, set the thread ids to 0 and the board posts to the main chat.

USAGE

    living_board.py show    --topic brief
    living_board.py set     --topic brief --title "Short human title" --file item.md
    living_board.py resolve --topic needs-me --item "substring of title"

Note on cleanup: this tool never deletes history. It converges the channel going forward, and
old messages age out of view above the pinned board. An early version grew a delete command and
it was used to probe the API against real messages, destroying four of them. Deleting someone's
history is not a capability test, and the board does not need it to work.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:  # Python 3.11+ stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

CONFIG_PATH = Path(os.environ.get("BOARD_CONFIG", Path.home() / ".hermes/board.toml"))
STATE_DIR = Path(os.environ.get("BOARD_STATE_DIR", Path.home() / ".hermes/state/boards"))

TG_LIMIT = 3900  # platform hard cap is 4096; leave room for the footer
ITEM_LIMIT = 600

# A board item is a HEADLINE plus the one thing that changed, not an essay. Measured on the
# first real pass after deployment: a single item ran 3,287 characters and pushed the board to
# 3,647 of the 4,096 hard cap, so one more finding would have truncated it. Long-form reasoning
# belongs in the project log. The cap is ENFORCED rather than advised because a prompt asking
# for brevity does not survive contact with an agent that just did interesting work.


def _parse_minimal_toml(text: str) -> dict:
    """Tiny TOML reader for the handful of keys this config uses.

    Exists so the tool runs on a stock Python 3.9 (still the system default on macOS) without
    asking the user to pip-install anything. Handles top-level scalars and one level of
    [section] tables, which is the entire shape of board.toml. Anything more exotic should use
    a real parser via `pip install tomli`.
    """
    cfg: dict = {}
    target = cfg
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            target = cfg.setdefault(line[1:-1].strip(), {})
            continue
        if "=" not in line:
            continue
        key, val = (p.strip() for p in line.split("=", 1))
        if val.startswith(('"', "'")):
            target[key] = val.strip("\"'")
        elif val.lower() in ("true", "false"):
            target[key] = val.lower() == "true"
        else:
            try:
                target[key] = int(val)
            except ValueError:
                target[key] = val
    return cfg


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"No board config at {CONFIG_PATH}.\n"
            "Copy templates/board.toml from this skill and fill in your chat and topics."
        )
    text = CONFIG_PATH.read_text()
    cfg = tomllib.loads(text) if tomllib else _parse_minimal_toml(text)
    for key in ("chat_id", "topics"):
        if key not in cfg:
            raise SystemExit(f"board config missing required key: {key}")
    cfg.setdefault("bot_token_env", "TELEGRAM_BOT_TOKEN")
    cfg.setdefault("timezone", "UTC")
    return cfg


def bot_token(cfg: dict) -> str:
    """Token comes from the environment, never from the config file.

    The config is safe to commit and share; the token is not. Keeping them separate means a
    board config can live in a dotfiles repo without leaking credentials.
    """
    env_name = cfg["bot_token_env"]
    tok = os.environ.get(env_name)
    if tok:
        return tok
    # Fall back to a dotenv-style file next to the config, for agents whose cron env is bare.
    env_file = CONFIG_PATH.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{env_name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{env_name} not set in environment or {env_file}")


def api(cfg: dict, method: str, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token(cfg)}/{method}",
        data=data,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


def now_local(cfg: dict) -> str:
    tz = timezone.utc
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(cfg["timezone"])
        except Exception:
            pass
    return datetime.now(tz).strftime("%a %-I:%M %p")


def state_path(topic: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{topic}.json"


def load_state(topic: str) -> dict:
    path = state_path(topic)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            # Do NOT silently start fresh. A blank state means message_id is lost, so the next
            # push orphans the existing pinned board and posts a second one, and every open
            # item silently vanishes from view. Preserve the file and make a human look.
            backup = path.with_suffix(f".corrupt-{int(datetime.now(timezone.utc).timestamp())}")
            path.replace(backup)
            raise SystemExit(
                f"Board state for {topic!r} is corrupt: {exc}\n"
                f"Preserved at {backup}\n"
                "Fix or delete it. Starting fresh would orphan the pinned board and drop "
                "every open item without saying so."
            )
    return {"message_id": None, "items": []}


def save_state(topic: str, state: dict) -> None:
    path = state_path(topic)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)  # atomic; a crash mid-write cannot corrupt the board


def escape_md(text: str) -> str:
    """Neutralize Telegram legacy-Markdown control characters in user content.

    The board is sent with parse_mode="Markdown", and the template supplies its own
    emphasis (**bold** headers, _italic_ timestamp). Item titles and bodies are
    arbitrary text: a single unmatched `_`, `*`, `[`, or backtick in a title makes
    Telegram reject the ENTIRE message with "can't parse entities", so one stray
    underscore in one item silently blanks the whole board. Escaping content — but
    not the template's own markup — keeps the board sendable no matter what a pass
    writes into it.
    """
    for ch in ("\\", "_", "*", "[", "`"):
        text = text.replace(ch, "\\" + ch)
    return text


def truncate_escaped(text: str, budget: int) -> str:
    """Truncate `text` so its ESCAPED form fits `budget` characters, then mark the cut.

    Slicing raw text and escaping afterwards can overshoot, because each escaped
    character costs two. Worse, a naive slice of already-escaped text can land between
    a backslash and the character it escapes, leaving a dangling escape that makes
    Telegram reject the message. Building up character by character against the escaped
    cost avoids both.
    """
    out, used = [], 0
    for ch in text:
        cost = len(escape_md(ch))
        if used + cost > budget:
            break
        out.append(ch)
        used += cost
    return "".join(out).rstrip() + " […]"


def render(cfg: dict, topic: str, state: dict, _depth: int = 0) -> str:
    """Render open items only. Resolved items are GONE, not struck through.

    A resolved item left visible is still something the reader has to process and dismiss,
    which is the exact cost this tool exists to remove.
    """
    items = [i for i in state.get("items", []) if not i.get("resolved")]
    stamp = now_local(cfg)
    decision = bool(cfg.get("topics_decision", {}).get(topic)) or topic in (
        "needs-me",
        "decisions",
    )

    if not items:
        empty = "Nothing needs you." if decision else "Nothing material since your last look."
        return f"{'✅' if decision else '🧭'} **{empty}**\n\n_as of {stamp}_"

    if decision:
        header = f"🚦 **{len(items)} waiting on you**"
    else:
        header = "🧭 **What changed**"

    parts = [header, ""]
    for idx, item in enumerate(items, 1):
        parts.append(f"**{idx}. {escape_md(item.get('title', '').strip())}**")
        body = item.get("body", "").strip()
        if body:
            parts.append(escape_md(body))
        parts.append("")
    parts.append(f"_as of {stamp}_")
    out = "\n".join(parts)

    if len(out) > TG_LIMIT and _depth == 0:
        # Never truncate mid-item; drop whole trailing items and say how many.
        keep, running = [], len(header) + 60
        for item in items:
            # Estimate against the ESCAPED length: escaping can only grow the text,
            # so measuring the raw string would under-count and keep too many rows.
            size = (
                len(escape_md(item.get("title", "")))
                + len(escape_md(item.get("body", "")))
                + 12
            )
            if running + size > TG_LIMIT - 120:
                break
            keep.append(item)
            running += size

        # If not even ONE item fits, keeping the first anyway is mandatory. Rendering an empty
        # list here would print "Nothing needs you" while items are actually waiting, which is
        # the worst possible failure for a board whose entire job is to be trusted when empty.
        # A single over-long item gets hard-truncated instead, with the cut marked.
        if not keep:
            first = dict(items[0])
            # Budget against ESCAPED lengths, matching the multi-item estimate above.
            # A raw-length budget under-counts (escaping only grows text), so a body of
            # mostly Markdown-sensitive characters would blow past the cap and fall
            # through to the hard slice.
            budget = TG_LIMIT - len(header) - len(escape_md(first.get("title", ""))) - 220
            first["body"] = truncate_escaped(first.get("body", ""), max(budget, 0))
            keep = [first]

        dropped = len(items) - len(keep)
        trimmed = render(cfg, topic, {**state, "items": keep}, _depth=1)
        if dropped:
            trimmed = trimmed.replace(
                f"_as of {stamp}_", f"_+{dropped} more in the project log · as of {stamp}_"
            )
        # Final belt-and-braces guard: the platform rejects the whole message if it is over
        # the hard cap, so a board that cannot be shortened must still be sendable.
        # The slice must not land between a backslash and the character it escapes —
        # a dangling escape makes Telegram reject the message outright, which is the
        # exact failure this guard exists to prevent.
        if len(trimmed) > TG_LIMIT:
            cut = trimmed[: TG_LIMIT - 4]
            if len(cut) - len(cut.rstrip("\\")) & 1:  # odd trailing backslashes = dangling
                cut = cut[:-1]
            trimmed = cut.rstrip() + " […]"
        out = trimmed
    return out


def try_pin(cfg: dict, topic: str, state: dict) -> None:
    """Pin the board, recording success so a failure is retried on the next run.

    Pinning can fail for a recoverable reason (the bot hasn't been granted pin
    permission yet, or Telegram had a blip). Since later runs edit the existing
    message rather than re-posting, a failure here would otherwise never be retried
    and the board would scroll out of view forever. A board that is merely unpinned
    still works, so warn rather than fail the run — but never report success silently.
    """
    pin = api(cfg, "pinChatMessage", chat_id=cfg["chat_id"],
              message_id=state["message_id"], disable_notification=True)
    state["pinned"] = bool(pin.get("ok"))
    save_state(topic, state)
    if not state["pinned"]:
        print(
            f"warning: board posted but NOT pinned ({pin.get('description')}). "
            "Grant the bot pin permission; this retries on the next run.",
            file=sys.stderr,
        )


def push(cfg: dict, topic: str, state: dict) -> None:
    """Edit the board in place; create and pin it if it does not exist yet."""
    text = render(cfg, topic, state)
    mid = state.get("message_id")

    if mid:
        res = api(cfg, "editMessageText", chat_id=cfg["chat_id"], message_id=mid,
                  text=text, parse_mode="Markdown")
        if res.get("ok") or "not modified" in res.get("description", ""):
            # An earlier run posted the board but couldn't pin it (no permission yet, or
            # a transient failure). Edits keep succeeding forever, so without this retry
            # the board would stay unpinned and scroll away permanently.
            if not state.get("pinned"):
                try_pin(cfg, topic, state)
            return
        # Board was deleted or is too old to edit; fall through and create a new one.

    params = {"chat_id": cfg["chat_id"], "text": text, "parse_mode": "Markdown"}
    thread = cfg["topics"].get(topic)
    if thread:
        params["message_thread_id"] = thread
    res = api(cfg, "sendMessage", **params)
    if not res.get("ok"):
        raise SystemExit(f"send failed: {res.get('description')}")

    state["message_id"] = res["result"]["message_id"]
    state["pinned"] = False
    # Save BEFORE pinning: the message exists now, so the id must be persisted even if
    # pinning fails. Losing it here would orphan the board and post a duplicate next run.
    save_state(topic, state)
    try_pin(cfg, topic, state)


def cmd_set(cfg: dict, args) -> int:
    """Add or update ONE item, matched by title.

    Matching by title is what makes re-running a pass idempotent: surfacing the same concern
    again updates the existing item instead of stacking a duplicate. Keep titles stable.
    """
    body = ""
    if args.file:
        body = Path(args.file).read_text().strip()
    elif args.body:
        body = args.body

    if len(body) > ITEM_LIMIT and not args.long:
        raise SystemExit(
            f"Item body is {len(body)} chars; the board cap is {ITEM_LIMIT}.\n"
            "The board is a dashboard, not a report. Write the headline and the one thing\n"
            "that changed, and put the reasoning in the project log.\n"
            "If it genuinely needs the length, pass --long."
        )

    state = load_state(args.topic)
    stamp = datetime.now(timezone.utc).isoformat()
    for item in state["items"]:
        if item["title"].lower() == args.title.lower():
            item.update(body=body, resolved=False, updated=stamp)
            break
    else:
        state["items"].append(
            {"title": args.title, "body": body, "resolved": False,
             "created": stamp, "updated": stamp}
        )

    save_state(args.topic, state)
    push(cfg, args.topic, state)
    open_n = len([i for i in state["items"] if not i.get("resolved")])
    print(f"board updated: {args.topic} ({open_n} open)")
    return 0


def cmd_resolve(cfg: dict, args) -> int:
    """Mark an item resolved so it leaves the board. Substring match on title."""
    state = load_state(args.topic)
    for item in state["items"]:
        if args.item.lower() in item["title"].lower() and not item.get("resolved"):
            item["resolved"] = True
            item["resolved_at"] = datetime.now(timezone.utc).isoformat()
            save_state(args.topic, state)
            push(cfg, args.topic, state)
            print(f"resolved: {item['title']}")
            return 0
    print(f"no open item matching {args.item!r}")
    return 1


def cmd_show(cfg: dict, args) -> int:
    state = load_state(args.topic)
    print(f"--- {args.topic} (message_id={state.get('message_id')}) ---")
    print(render(cfg, args.topic, state))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Living board for chat notifications")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def topic_arg(sp):
        sp.add_argument("--topic", required=True)

    p_set = sub.add_parser("set", help="add or update an item")
    topic_arg(p_set)
    p_set.add_argument("--title", required=True)
    p_set.add_argument("--body")
    p_set.add_argument("--file")
    p_set.add_argument("--long", action="store_true", help="allow a body over the cap")
    p_set.set_defaults(func=cmd_set)

    p_res = sub.add_parser("resolve", help="remove an item from the board")
    topic_arg(p_res)
    p_res.add_argument("--item", required=True)
    p_res.set_defaults(func=cmd_resolve)

    p_show = sub.add_parser("show", help="print the board without sending")
    topic_arg(p_show)
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    cfg = load_config()
    if args.topic not in cfg["topics"]:
        raise SystemExit(f"unknown topic {args.topic!r}; config has: {list(cfg['topics'])}")
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
