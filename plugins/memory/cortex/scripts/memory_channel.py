#!/usr/bin/env python3
"""Memory Management channel: auto-created Telegram topic for memory escalations.

Each agent gets a dedicated forum topic in its home group where the weekly
curation report and any ``needs_human`` escalations are posted. The topic is
created once via the Bot API and its ID cached, so subsequent runs reuse it.

Requires the bot to be an administrator with ``can_manage_topics`` in a
forum-enabled supergroup.

Design notes:
  * Never silently drop an escalation. If topic creation fails, fall back to
    the group's General thread and flag the degradation.
  * Cache lives beside the profile so it survives restarts.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

TOPIC_NAME = "🧠 Memory Management"
API = "https://api.telegram.org/bot%s/%s"


class MemoryChannel:
    def __init__(self, token: str, chat_id: str, cache_path: str | Path):
        self.token = token
        self.chat_id = str(chat_id)
        self.cache_path = Path(cache_path)

    # -- low-level ---------------------------------------------------------

    def _call(self, method: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            API % (self.token, method),
            data=json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"ok": False, "description": "HTTP %s" % e.code}
        except Exception as e:  # network, timeout, DNS
            return {"ok": False, "description": str(e)}

    # -- cache -------------------------------------------------------------

    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self, data: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2))

    # -- topic management --------------------------------------------------

    def preflight(self) -> dict:
        """Verify the bot can actually manage topics here. Cheap, no writes."""
        me = self._call("getMe")
        if not me.get("ok"):
            return {"ok": False, "reason": "getMe failed: %s" % me.get("description")}
        chat = self._call("getChat", {"chat_id": self.chat_id})
        if not chat.get("ok"):
            return {"ok": False, "reason": "getChat failed: %s" % chat.get("description")}
        is_forum = bool(chat["result"].get("is_forum"))
        member = self._call("getChatMember", {"chat_id": self.chat_id, "user_id": me["result"]["id"]})
        can_manage = bool(member.get("result", {}).get("can_manage_topics")) if member.get("ok") else False
        status = member.get("result", {}).get("status") if member.get("ok") else None
        return {
            "ok": is_forum and can_manage,
            "is_forum": is_forum,
            "status": status,
            "can_manage_topics": can_manage,
            "bot": me["result"].get("username"),
            "reason": "" if (is_forum and can_manage) else "needs forum group + admin with can_manage_topics",
        }

    def ensure_topic(self, name: str = TOPIC_NAME) -> dict:
        """Return the memory-management topic id, creating it once if needed."""
        cache = self._load_cache()
        key = "memory_topic_%s" % self.chat_id
        if cache.get(key):
            return {"ok": True, "thread_id": cache[key], "created": False}

        pre = self.preflight()
        if not pre["ok"]:
            return {"ok": False, "reason": pre["reason"], "thread_id": None}

        res = self._call("createForumTopic", {
            "chat_id": self.chat_id,
            "name": name,
            "icon_color": 0x6FB9F0,
        })
        if not res.get("ok"):
            return {"ok": False, "reason": res.get("description", "createForumTopic failed"), "thread_id": None}
        thread_id = res["result"]["message_thread_id"]
        cache[key] = thread_id
        self._save_cache(cache)
        return {"ok": True, "thread_id": thread_id, "created": True}

    # -- posting -----------------------------------------------------------

    def post(self, text: str, thread_id: int | None = None, allow_fallback: bool = True) -> dict:
        """Post to the memory topic. Falls back to General rather than dropping."""
        if thread_id is None:
            t = self.ensure_topic()
            thread_id = t.get("thread_id")
            if not t["ok"] and not allow_fallback:
                return {"ok": False, "reason": t.get("reason")}

        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        if thread_id:
            payload["message_thread_id"] = thread_id
        res = self._call("sendMessage", payload)
        if res.get("ok"):
            return {"ok": True, "thread_id": thread_id, "degraded": False}

        if allow_fallback and thread_id:
            # Topic may have been deleted; retry in General so nothing is lost.
            payload.pop("message_thread_id", None)
            res2 = self._call("sendMessage", payload)
            if res2.get("ok"):
                return {"ok": True, "thread_id": None, "degraded": True,
                        "reason": "topic post failed (%s); fell back to General"
                                  % res.get("description")}
        return {"ok": False, "reason": res.get("description", "sendMessage failed")}


def format_escalation(item: dict) -> str:
    """Render one needs_human item with enough context to decide in one read."""
    lines = ["*Memory needs your call* — `%s`" % item["id"], "", "*%s*" % item["title"]]
    if item.get("detail"):
        lines += ["", item["detail"]]
    if item.get("sources"):
        lines += ["", "*Sources:*"] + ["• `%s`" % s for s in item["sources"]]
    if item.get("recommendation"):
        lines += ["", "*Recommended:* %s" % item["recommendation"]]
    age = item.get("created", "")
    lines += ["", "_first seen %s · seen %dx_" % (age, item.get("seen_count", 1))]
    return "\n".join(lines)


def load_token(env_path: str) -> str | None:
    try:
        for line in open(env_path):
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def resolve_owner_group(config_path: str) -> str | None:
    """Find the agent's private group with its owner.

    Each agent has a private working group with the person it reports to.
    ``free_response_chats`` is the reliable marker: it is the chat where the
    agent replies without needing a mention, i.e. its owner's channel. Falls
    back to the first negative (group) id in ``group_allowed_chats``.

    Returns a chat id string, or None if it cannot be determined -- callers
    must treat None as "do not post" rather than guessing a destination.
    """
    try:
        import yaml
    except ImportError:
        return None
    try:
        cfg = yaml.safe_load(open(config_path)) or {}
    except (OSError, Exception):
        return None

    tg = ((cfg.get("channels") or {}).get("telegram")) or cfg.get("telegram") or {}
    free = str(tg.get("free_response_chats") or "").strip()
    if free:
        first = free.split(",")[0].strip()
        if first:
            return first
    groups = str(tg.get("group_allowed_chats") or "").strip()
    for cid in groups.split(","):
        cid = cid.strip()
        if cid.startswith("-"):  # negative ids are groups/supergroups
            return cid
    # Some profiles only declare their group via channel_overrides or a plain
    # allowed_chats list. If there is exactly one group id, it is unambiguous.
    overrides = [k for k in (tg.get("channel_overrides") or {}) if str(k).startswith("-")]
    if len(overrides) == 1:
        return str(overrides[0])
    allowed = [c.strip() for c in str(tg.get("allowed_chats") or "").split(",") if c.strip().startswith("-")]
    if len(allowed) == 1:
        return allowed[0]
    return None


__all__ = ["MemoryChannel", "format_escalation", "load_token", "resolve_owner_group", "TOPIC_NAME"]
