"""Tests for the cortex pre-compress handoff digest.

Two layers:

  • `handoff.py` pure functions — extraction is deterministic and has no Hermes
    dependencies, so we test it directly (build_handoff, extract_artifacts,
    handoff_slug).
  • Store round-trip — a handoff written via CortexStore.write_page lands under
    the handoff/ category, is readable back, and is FTS5-searchable. This proves
    the "survives compaction + re-pulled by prefetch" claim at the store layer
    without needing the full Hermes agent runtime.

Run from the repo root::

    pytest plugins/memory/cortex/tests/test_handoff.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the plugin importable without installing the repo as a package
# (mirrors test_thread_safety.py).
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from handoff import (  # noqa: E402
    build_handoff,
    extract_artifacts,
    handoff_slug,
)
from store import CortexStore  # noqa: E402
from retrieval import CortexRetriever  # noqa: E402


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _sample_thread() -> list[dict]:
    return [
        _msg("user", "We need to build a pre-compaction handoff for cortex."),
        _msg(
            "assistant",
            "Decided: we'll implement it as an upgrade to cortex.on_pre_compress "
            "rather than a second provider. The plan is to write to handoff/.",
        ),
        _msg("user", "ok build it. The trusted source is ~/src/hermes-config."),
        _msg(
            "assistant",
            "I'll edit plugins/memory/cortex/__init__.py and add handoff.py. "
            "Next step: write tests, then open a PR at https://github.com/TechNickAI/hermes-config.",
        ),
        _msg("user", "ok"),  # trivial tail — must be skipped as the goal
    ]


# --------------------------------------------------------------------------- #
# Pure extraction
# --------------------------------------------------------------------------- #

def test_build_handoff_has_all_sections() -> None:
    body = build_handoff(_sample_thread(), topic_label="Project Thread", now_str="2026-06-01 00:30")
    for heading in ("## GOAL", "## STATE", "## DECISIONS", "## OPEN LOOPS", "## ARTIFACTS"):
        assert heading in body, f"missing {heading}"
    # Topic + timestamp metadata present.
    assert "Project Thread" in body
    assert "2026-06-01 00:30" in body


def test_goal_skips_trivial_tail() -> None:
    # The final "ok" must not become the GOAL; the substantive prior user
    # message should win.
    body = build_handoff(_sample_thread())
    assert "pre-compaction handoff" in body
    # The bare "ok" should not be the headline goal line.
    goal_section = body.split("## GOAL", 1)[1].split("##", 1)[0]
    assert goal_section.strip() != "ok"


def test_extract_artifacts_finds_paths_and_urls() -> None:
    arts = extract_artifacts(_sample_thread())
    assert any("hermes-config" in a for a in arts)
    assert any(a.startswith("https://github.com/TechNickAI") for a in arts)
    assert any(a.endswith("__init__.py") for a in arts)


def test_extract_artifacts_dedupes_and_caps() -> None:
    msgs = [_msg("user", "see /a/b/c.py and /a/b/c.py and /a/b/c.py")]
    arts = extract_artifacts(msgs)
    assert arts.count("/a/b/c.py") == 1


def test_artifacts_strip_trailing_punctuation() -> None:
    msgs = [_msg("assistant", "Look at https://example.com/foo, then /x/y.md.")]
    arts = extract_artifacts(msgs)
    assert "https://example.com/foo" in arts
    assert "/x/y.md" in arts


def test_build_handoff_empty_messages_returns_empty() -> None:
    assert build_handoff([]) == ""


def test_build_handoff_ignores_non_text_content() -> None:
    # List-style content parts (vision/tool) should not crash extraction.
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "build the thing in /p/q.py"}]},
        {"role": "assistant", "content": "ok"},
    ]
    body = build_handoff(msgs)
    assert "/p/q.py" in body


def test_handoff_slug_is_topic_stable() -> None:
    # Same chat+thread → same slug (so re-compactions overwrite in place).
    a = handoff_slug(chat_id="-1002", thread_id="37")
    b = handoff_slug(chat_id="-1002", thread_id="37")
    assert a == b == "handoff-n1002-37"
    # Different thread → different slug.
    assert handoff_slug(chat_id="-1002", thread_id="38") != a


def test_handoff_slug_falls_back_to_session() -> None:
    assert handoff_slug(session_id="sess123") == "handoff-sess123"
    assert handoff_slug() == "handoff-default"


def test_handoff_slug_negative_chat_id_no_collision() -> None:
    # Telegram group chat IDs are negative. A negative chat id must NOT collide
    # with its positive counterpart (regression: strip("-") used to drop the
    # sign, mapping -1002/37 and 1002/37 to the same page).
    neg = handoff_slug(chat_id="-1002", thread_id="37")
    pos = handoff_slug(chat_id="1002", thread_id="37")
    assert neg != pos
    # Negative sign is encoded, not dropped.
    assert neg == "handoff-n1002-37"
    assert pos == "handoff-1002-37"


def test_handoff_slug_sanitizes() -> None:
    slug = handoff_slug(chat_id="weird/id:value", thread_id="")
    assert "/" not in slug and ":" not in slug
    assert slug.startswith("handoff-")


# --------------------------------------------------------------------------- #
# Store round-trip — the "survives + searchable" guarantee
# --------------------------------------------------------------------------- #

def test_handoff_writeback_roundtrip(tmp_path: Path) -> None:
    store = CortexStore(store_path=str(tmp_path / "cortex"))
    try:
        body = build_handoff(_sample_thread(), topic_label="T", now_str="2026-06-01 00:30")
        rel = store.write_page(
            category="handoff",
            slug_or_title=handoff_slug(chat_id="-1002", thread_id="37"),
            body=body,
            tags=["handoff", "auto", "pre-compress"],
            title="Handoff — T",
        )
        # Lands under handoff/ with the stable slug.
        assert rel == "handoff/handoff-n1002-37.md"

        # Readable back with content intact.
        page = store.get_page(rel)
        assert page is not None
        assert "## GOAL" in page["body"]

        # FTS5-searchable → prefetch will find it next turn.
        retriever = CortexRetriever(store)
        hits = retriever.search("handoff cortex", limit=5)
        assert any(h["rel_path"] == rel for h in hits)
    finally:
        store.close()


def test_handoff_rewrite_is_idempotent_in_place(tmp_path: Path) -> None:
    """Re-compaction of the same topic overwrites, not duplicates."""
    store = CortexStore(store_path=str(tmp_path / "cortex"))
    try:
        slug = handoff_slug(chat_id="-1002", thread_id="37")
        rel1 = store.write_page(category="handoff", slug_or_title=slug, body="first", title="H")
        rel2 = store.write_page(category="handoff", slug_or_title=slug, body="second", title="H")
        assert rel1 == rel2
        page = store.get_page(rel1)
        assert page is not None and "second" in page["body"]
        # Exactly one page in the handoff category.
        pages = store.list_pages(category="handoff", limit=50)
        assert len(pages) == 1
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Provider integration — requires the Hermes runtime (agent.memory_provider).
# Skipped automatically when run outside a Hermes checkout.
# --------------------------------------------------------------------------- #

def _load_provider_class():
    """Import CortexMemoryProvider, making the Hermes runtime importable.

    Returns None if `agent.memory_provider` can't be found (e.g. CI without a
    Hermes checkout), so the test self-skips rather than failing.
    """
    import importlib
    import os

    candidates = [
        os.environ.get("HERMES_AGENT_DIR"),
        os.path.expanduser("~/.hermes/hermes-agent"),
    ]
    for path in candidates:
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    # Make `cortex` importable as a package (parent of the plugin dir).
    pkg_parent = str(PLUGIN_DIR.parent)
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)
    try:
        mod = importlib.import_module("cortex")
        return mod.CortexMemoryProvider
    except Exception:
        return None


def test_cli_session_writes_handoff_via_session_id(tmp_path: Path) -> None:
    """A CLI session (no chat/thread, only session_id) still gets a handoff.

    Regression for the Codex review note: the hook must not gate on
    chat_id/thread_id being present — substance, not surface, decides.
    """
    Provider = _load_provider_class()
    if Provider is None:
        pytest.skip("Hermes runtime (agent.memory_provider) not importable")

    store_dir = str(tmp_path / "cortex")
    p = Provider(config={"store_path": store_dir})
    # No chat_id / thread_id — pure CLI-style init.
    p.initialize(session_id="cli-sess-9", hermes_home=str(tmp_path))
    try:
        out = p.on_pre_compress(_sample_thread())
        # A handoff page keyed by session_id was written and surfaced.
        assert "handoff saved to" in out
        assert "handoff-cli-sess-9" in out
        pages = list((tmp_path / "cortex" / "handoff").glob("*.md"))
        assert any("cli-sess-9" in pth.name for pth in pages)
    finally:
        p.shutdown()


def test_gateway_session_keys_handoff_by_topic(tmp_path: Path) -> None:
    """A gateway session keys the handoff by chat+thread (the durable topic)."""
    Provider = _load_provider_class()
    if Provider is None:
        pytest.skip("Hermes runtime (agent.memory_provider) not importable")

    p = Provider(config={"store_path": str(tmp_path / "cortex")})
    p.initialize(
        session_id="s1",
        hermes_home=str(tmp_path),
        chat_id="-1002",
        thread_id="37",
        chat_name="Project Thread",
    )
    try:
        out = p.on_pre_compress(_sample_thread())
        assert "handoff-n1002-37" in out
    finally:
        p.shutdown()

