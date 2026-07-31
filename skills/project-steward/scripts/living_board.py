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
import shutil
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
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
LOCK_TIMEOUT = 30  # seconds to wait for another run to release a board
LOCK_STALE_SECONDS = 300  # a lock older than this is assumed abandoned by a crashed run

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
    stamp = datetime.now(tz)
    # %-I is POSIX-only and raises ValueError on Windows, which would crash every render.
    hour = stamp.hour % 12 or 12
    return f"{stamp.strftime('%a')} {hour}:{stamp.strftime('%M %p')}"


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


def _esc(text: str) -> str:
    """Escape for Telegram's HTML parse mode.

    Only these three characters are special in HTML mode, so escaping is total and
    predictable. Markdown mode cannot be made safe the same way: item titles and bodies are
    arbitrary agent prose, and a stray underscore, asterisk, bracket or backtick either
    reformats the board or makes Telegram reject the message outright with an entity-parsing
    error. A rejection means the update cannot be posted at all, since the same text is
    reused by the fallback sendMessage. Plain text avoids that but loses bold headings; HTML
    keeps the formatting and stays safe.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _assemble(header: str, items: list, footer: str) -> str:
    parts = [header, ""]
    for idx, item in enumerate(items, 1):
        parts.append(f"<b>{idx}. {_esc(item.get('title', '').strip())}</b>")
        body = item.get("body", "").strip()
        if body:
            parts.append(_esc(body))
        parts.append("")
    parts.append(footer)
    return "\n".join(parts)


def render(cfg: dict, topic: str, state: dict) -> str:
    """Render open items only. Resolved items are GONE, not struck through.

    A resolved item left visible is still something the reader has to process and dismiss,
    which is the exact cost this tool exists to remove.

    Overflow is handled by MEASURING the assembled text rather than estimating per-item
    sizes. An earlier version estimated (title + body + 12), which omitted markdown, the
    index, and newlines, so it kept too many items and a final hard slice cut mid-item,
    violating the rule it was supposed to enforce. It also recursed to re-render, which
    recomputed the timestamp and produced a header counting the TRIMMED list, so a decision
    board could under-report how many items were waiting.
    """
    items = [i for i in state.get("items", []) if not i.get("resolved")]
    stamp = now_local(cfg)
    decision = bool(cfg.get("topics_decision", {}).get(topic)) or topic in (
        "needs-me",
        "decisions",
    )

    if not items:
        empty = "Nothing needs you." if decision else "Nothing material since your last look."
        return f"{'✅' if decision else '🧭'} <b>{empty}</b>\n\n<i>as of {stamp}</i>"

    # The header always reflects the TRUE number of open items, never the trimmed subset.
    header = f"🚦 <b>{len(items)} waiting on you</b>" if decision else "🧭 <b>What changed</b>"
    footer = f"<i>as of {stamp}</i>"

    out = _assemble(header, items, footer)
    if len(out) <= TG_LIMIT:
        return out

    # Drop whole trailing items, measuring the real assembled length each time.
    keep = list(items)
    while keep:
        dropped = len(items) - len(keep)
        candidate = _assemble(
            header, keep, f"<i>+{dropped} more in the project log · {stamp}</i>" if dropped else footer
        )
        if len(candidate) <= TG_LIMIT:
            return candidate
        keep.pop()

    # Not even one whole item fits. Rendering an empty list here would print "Nothing needs
    # you" while items are waiting, the worst possible failure for a board whose value is
    # being trusted when it says empty. Truncate the single item's BODY instead, which keeps
    # its title and the true count intact, and mark the cut.
    first = dict(items[0])
    dropped = len(items) - 1
    tail = f"<i>+{dropped} more in the project log · {stamp}</i>" if dropped else footer
    overhead = len(_assemble(header, [{**first, "body": ""}], tail))
    first["body"] = first.get("body", "")[: max(TG_LIMIT - overhead - 8, 0)].rstrip() + " […]"
    return _assemble(header, [first], tail)


def push(cfg: dict, topic: str, state: dict) -> None:
    """Edit the board in place; create and pin it if it does not exist yet.

    Sent as HTML, with every agent-supplied field escaped by _esc. See _esc for why HTML
    rather than Markdown: only three characters are special, so escaping is total.
    """
    text = render(cfg, topic, state)
    mid = state.get("message_id")

    if mid:
        res = api(cfg, "editMessageText", chat_id=cfg["chat_id"], message_id=mid,
                  text=text, parse_mode="HTML")
        if res.get("ok") or "not modified" in res.get("description", ""):
            return
        # Board was deleted or is too old to edit; fall through and create a new one.

    params = {"chat_id": cfg["chat_id"], "text": text, "parse_mode": "HTML"}
    thread = cfg["topics"].get(topic)
    if thread:
        params["message_thread_id"] = thread
    res = api(cfg, "sendMessage", **params)
    if not res.get("ok"):
        raise SystemExit(f"send failed: {res.get('description')}")

    state["message_id"] = res["result"]["message_id"]
    save_state(topic, state)  # persist the id first: an unsaved id orphans the message

    pin = api(cfg, "pinChatMessage", chat_id=cfg["chat_id"],
              message_id=state["message_id"], disable_notification=True)
    if not pin.get("ok"):
        # Not fatal (the board still works unpinned), but silence here would leave the board
        # permanently unpinned with the command reporting success, since every later update
        # takes the edit path and never retries the pin.
        print(
            f"WARNING: board posted but could not be pinned: {pin.get('description')}\n"
            "Grant the bot pin permission, then delete the board state file to re-create it.",
            file=sys.stderr,
        )


@contextmanager
def board_lock(topic: str):
    """Serialize the whole read-modify-push cycle for one board.

    Two scheduled passes calling `set` on the same board can otherwise both load the same
    snapshot, each append their own item, and the second write silently erases the first.
    Atomic file replacement prevents a half-written file, not a lost update.

    Uses an exclusive-create lock directory, which is atomic on every POSIX filesystem and
    on Windows. A lock older than LOCK_STALE_SECONDS is assumed abandoned by a crashed run.
    """
    path = state_path(topic).with_suffix(".lock")
    deadline = time.time() + LOCK_TIMEOUT
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue  # released between the failed mkdir and the stat
            if age > LOCK_STALE_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
                continue
            if time.time() > deadline:
                raise SystemExit(
                    f"Board {topic!r} is locked by another run ({int(age)}s old).\n"
                    f"If nothing else is running, remove {path}"
                )
            time.sleep(0.2)
    try:
        yield
    finally:
        shutil.rmtree(path, ignore_errors=True)


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

    with board_lock(args.topic):
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

        # Push BEFORE saving: if the platform rejects the update, on-disk state must not
        # claim a change the pinned board never received. push() saves the message id itself
        # when it has to create a new board.
        push(cfg, args.topic, state)
        save_state(args.topic, state)

    open_n = len([i for i in state["items"] if not i.get("resolved")])
    print(f"board updated: {args.topic} ({open_n} open)")
    return 0


def cmd_resolve(cfg: dict, args) -> int:
    """Mark an item resolved so it leaves the board. Substring match on title."""
    with board_lock(args.topic):
        state = load_state(args.topic)
        for item in state["items"]:
            if args.item.lower() in item["title"].lower() and not item.get("resolved"):
                item["resolved"] = True
                item["resolved_at"] = datetime.now(timezone.utc).isoformat()
                push(cfg, args.topic, state)
                save_state(args.topic, state)
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
