"""Tests for the telegram-agent-steward classifier and archive guarantees.

These run from a bare clone: no hermes-agent, no telethon, no network.
The classification and archive functions are pure and importable on their own.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "telegram-agent-steward"
    / "scripts"
    / "telegram_agent_steward.py"
)


@pytest.fixture(scope="module")
def steward():
    spec = importlib.util.spec_from_file_location("telegram_agent_steward", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["telegram_agent_steward"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------- alarms

# Real machine-emitted alarm shapes. Missing one of these means a live
# incident gets silently collapsed.
ALARMS = [
    "FAVORITE GRINDER HALTED - entry refused.\n  reason : SEV-1: double-submit",
    "\u26a0\ufe0f Cron 'acting guard' failed: exited code 2 stdout: GUARD BROKEN",
    "MONITOR BLIND   could not read the exchange: ApiError HTTP 500",
    "BOOK HALTED\n  HALT (forward-looking): realized loss exceeds headroom",
    "CRITICAL: disk full on /dev/sda1",
    "SEV-0 outage confirmed",
    "order rejected by exchange",
    "stale market data detected",
]

# Ordinary prose that merely discusses alarms. An early version of the regex
# matched any message containing "halt" or "escalate" and flagged 385 normal
# conversational messages as critical.
NOT_ALARMS = [
    "The halt cleared and entry is running on schedule again.",
    "I should escalate this to you before proceeding, but it can wait.",
    "That changes the picture, the executor is shared across ~12 modules.",
    "Here's the plain version, no jargon.",
    "Killed. The surveillance job is gone; we hold zero of that token.",
    "Done. Deployed and verified silent against live state.",
]


@pytest.mark.parametrize("text", ALARMS)
def test_real_alarms_are_never_touchable(steward, text):
    assert steward.NEVER_TOUCH.search(text), "real alarm must be protected"


@pytest.mark.parametrize("text", NOT_ALARMS)
def test_prose_about_alarms_is_not_an_alarm(steward, text):
    assert not steward.NEVER_TOUCH.search(text), "prose must not be flagged critical"


# ------------------------------------------------------------ ephemeral

EPHEMERAL = [
    "\U0001f4bb terminal cd /srv && ls",
    "\u23f3 Working - 3 min - terminal",
    "\u23f3 Queued for the next turn.",
    "\u23e9 Steered into current run.",
    "\U0001f4be Self-improvement review: Patched SKILL.md",
    "\U0001f40d Running code from tools import x",
    # U+270D and U+270F are different characters; both appear in the wild.
    "\u270d\ufe0f Writing ~/notes.md...",
    "\u270d Writing ~/other.md...",
    "\U0001f527 Editing ~/config.yaml...",
]

SUBSTANTIVE = [
    "That patch inserted broken syntax. Fixing immediately.",
    "Here is the answer: the root cause was a double-submit.",
    "",
]


@pytest.mark.parametrize("text", EPHEMERAL)
def test_ephemeral_ui_is_recognized(steward, text):
    assert steward.is_ephemeral(text)


@pytest.mark.parametrize("text", SUBSTANTIVE)
def test_substantive_text_is_not_ephemeral(steward, text):
    assert not steward.is_ephemeral(text)


# ------------------------------------------------------------ canonical


def test_canonical_preserves_digits(steward):
    """Digit normalization merges distinct orders/prices and is a data-loss bug."""
    a = steward.canonical("filled 25ct at 99c order 851")
    b = steward.canonical("filled 50ct at 12c order 902")
    assert a != b


def test_canonical_normalizes_only_whitespace(steward):
    assert steward.canonical("a   b\tc ") == "a b c"


# -------------------------------------------------------------- archive


def test_archive_round_trips_every_record(tmp_path, steward):
    recs = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
    assert steward.archive_and_verify(tmp_path / "a.jsonl", recs) is True
    lines = (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["id"] for x in lines] == [1, 2]


def test_archive_appends_without_losing_history(tmp_path, steward):
    p = tmp_path / "a.jsonl"
    assert steward.archive_and_verify(p, [{"id": 1, "text": "a"}]) is True
    assert steward.archive_and_verify(p, [{"id": 2, "text": "b"}]) is True
    assert len(p.read_text(encoding="utf-8").splitlines()) == 2


def test_empty_batch_is_trivially_safe(tmp_path, steward):
    assert steward.archive_and_verify(tmp_path / "none.jsonl", []) is True


# --------------------------------------------------------------- cursor


def test_cursor_round_trips_and_never_moves_backwards(tmp_path, steward):
    st = steward.State(tmp_path / "s.db")
    assert st.cursor_for(-100, 5) == 0
    st.set_cursor(-100, 5, 250, "2026-01-01T00:00:00+00:00")
    assert st.cursor_for(-100, 5) == 250
    # A stale run must not rewind the cursor and cause reprocessing.
    st.set_cursor(-100, 5, 100, "2026-01-01T01:00:00+00:00")
    assert st.cursor_for(-100, 5) == 250


def test_alarm_state_survives_and_tracks_ack(tmp_path, steward):
    st = steward.State(tmp_path / "s.db")
    st.observe(-100, "sig", "2026-01-01T00:00:00+00:00", "SEV-1 HALTED", n=54)
    row = st.get(-100, "sig")
    assert row[2] == 54 and row[3] == 0
    assert len(st.open_alarms(-100)) == 1
    st.ack(-100, "sig")
    assert st.get(-100, "sig")[3] == 1
    assert st.open_alarms(-100) == []


def test_observe_keeps_the_highest_count_seen(tmp_path, steward):
    st = steward.State(tmp_path / "s.db")
    st.observe(-100, "sig", "2026-01-01T00:00:00+00:00", "x", n=10)
    st.observe(-100, "sig", "2026-01-02T00:00:00+00:00", "x", n=3)
    assert st.get(-100, "sig")[2] == 10
