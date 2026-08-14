"""Behavioral guards for the imessage-bluebubbles agent CLI.

Every test here binds to a defect that shipped and was caught in review. The
common shape was a check that could not report the failure it existed to
catch, so each test asserts the FAILING direction, not just the happy path.

No network and no BlueBubbles server: `bb.api` is stubbed, so these run
anywhere CI does. Nothing here can send a message.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "imessage-bluebubbles"
    / "scripts"
    / "bb.py"
)
SPEC = importlib.util.spec_from_file_location("imessage_bluebubbles_bb", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


SERVER_INFO = {
    "data": {
        "server_version": "1.9.9",
        "os_version": "26.6.0",
        "private_api": True,
    }
}


def stub_api(monkeypatch, chats, message_pages=None):
    """Replace bb.api so no server is needed.

    `chats` is returned for chat/query. Passing a list of pages simulates
    pagination; a bare list is treated as a single page.
    """
    pages = chats if chats and isinstance(chats[0], list) else [chats]

    def fake_api(method, path, url, pw, **kwargs):
        if "chat/query" in path:
            offset = (kwargs.get("json") or {}).get("offset", 0)
            index = offset // 1000
            return {"data": pages[index] if index < len(pages) else []}
        if "server/info" in path:
            return SERVER_INFO
        if "/message" in path:
            return {"data": message_pages or []}
        return {"data": "pong"}

    monkeypatch.setattr(MODULE, "api", fake_api)


def chat(guid: str, address: str) -> dict:
    return {"guid": guid, "participants": [{"address": address}]}


# --------------------------------------------------------------- health


def test_health_fails_when_chat_access_is_empty(monkeypatch, capsys):
    """Server up and password fine, but chat.db unreadable, must NOT exit 0.

    This is the Full Disk Access failure the command exists to detect. It
    previously printed the warning and exited 0, so any caller gating on the
    exit status got a false pass on the one case that matters.
    """
    stub_api(monkeypatch, [])
    with pytest.raises(SystemExit) as exc:
        MODULE.cmd_health(None, "http://127.0.0.1:1234", "pw")
    assert exc.value.code != 0
    assert "NO DATA" in str(exc.value.code)


def test_health_succeeds_when_chats_are_visible(monkeypatch, capsys):
    stub_api(monkeypatch, [chat("iMessage;-;+15550000000", "+15550000000")])
    MODULE.cmd_health(None, "http://127.0.0.1:1234", "pw")
    assert "chat access: ok" in capsys.readouterr().out


# ----------------------------------------------------- recipient safety


def test_ambiguous_selector_is_refused(monkeypatch):
    """Never guess between candidates: texting the wrong person is final."""
    stub_api(
        monkeypatch,
        [chat(f"iMessage;-;+1555000{i:04d}", "+1555") for i in range(3)],
    )
    with pytest.raises(SystemExit) as exc:
        MODULE.resolve_chat("+1555", "http://x", "pw")
    assert "ambiguous" in str(exc.value.code).lower()


def test_unique_selector_resolves(monkeypatch):
    stub_api(monkeypatch, [chat("iMessage;-;+15550000000", "+15550000000")])
    assert (
        MODULE.resolve_chat("5550000000", "http://x", "pw")
        == "iMessage;-;+15550000000"
    )


def test_no_match_is_refused_not_guessed(monkeypatch):
    stub_api(monkeypatch, [chat("iMessage;-;+15550000000", "+15550000000")])
    with pytest.raises(SystemExit):
        MODULE.resolve_chat("nobody-by-that-name", "http://x", "pw")


def test_raw_guid_bypasses_lookup(monkeypatch):
    """A caller-supplied GUID is honored verbatim, by design.

    Documented so the bypass stays deliberate rather than becoming a
    surprise: an exact GUID is treated as the user's own resolution.
    """
    stub_api(monkeypatch, [])
    guid = "iMessage;-;+15550000000"
    assert MODULE.resolve_chat(guid, "http://x", "pw") == guid


def test_resolution_searches_past_the_first_page(monkeypatch):
    """chat/query caps at 1000 rows, so a single page is unsafe twice over.

    A real recipient past row 1000 reads as "no match", and worse, a selector
    that is ambiguous across the full account can look unique inside page one
    and let the guard send to a single wrong match.
    """
    page_one = [chat(f"iMessage;-;+1999{i:07d}", "+1999") for i in range(1000)]
    page_two = [chat("iMessage;-;+15550001234", "+15550001234")]
    stub_api(monkeypatch, [page_one, page_two])
    assert (
        MODULE.resolve_chat("5550001234", "http://x", "pw")
        == "iMessage;-;+15550001234"
    )


def test_ambiguity_is_detected_across_pages(monkeypatch):
    """Two matches split across pages must still refuse."""
    page_one = [chat(f"iMessage;-;+1888{i:07d}", "+1888") for i in range(999)]
    page_one.append(chat("iMessage;-;+15550009999", "+15550009999"))
    page_one = page_one[:1000]
    page_two = [chat("SMS;-;+15550009999", "+15550009999")]
    stub_api(monkeypatch, [page_one, page_two])
    with pytest.raises(SystemExit) as exc:
        MODULE.resolve_chat("5550009999", "http://x", "pw")
    assert "ambiguous" in str(exc.value.code).lower()
