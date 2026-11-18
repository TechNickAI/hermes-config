"""Offline contracts for the keyless archive reader."""
import importlib.util
import io
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/reddit-reading/scripts/reddit_reading.py"


def load():
    spec = importlib.util.spec_from_file_location("reddit_reading", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_ids_and_sample_ranking():
    reader = load()
    for value in ("x8i09x", "t3_x8i09x", "https://redd.it/x8i09x",
                  "https://old.reddit.com/r/test/comments/x8i09x/title/comment/"):
        assert reader.post_id(value) == "x8i09x"
    for value in ("https://example.com/x8i09x", "https://reddit.com/r/test/s/abc", "t1_abc"):
        with pytest.raises(ValueError):
            reader.post_id(value)
    posts = [{"id": "a", "score": 1}, {"id": "b", "score": 8}]
    assert reader.rank_posts(posts, 1) == [{"id": "b", "score": 8}]


def test_client_handles_failure_payload_and_retries(monkeypatch):
    reader = load()
    monkeypatch.setattr(reader.time, "sleep", lambda _: None)
    responses = iter([b'{"data": null, "error": "Query timed out"}'])
    monkeypatch.setattr(reader.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(next(responses)))
    with pytest.raises(RuntimeError, match="Query timed out"):
        reader.Client().get("/posts/ids", ids="abc")

    from urllib.error import HTTPError
    responses = iter([HTTPError("https://example.com", 422, "timeout", {}, None),
                      io.BytesIO(b'{"data": []}')])
    def open_fake(*a, **kw):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(reader.urllib.request, "urlopen", open_fake)
    assert reader.Client().get("/posts/search", subreddit="test") == []
