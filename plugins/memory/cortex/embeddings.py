"""Embedding client + vector math for Cortex hybrid retrieval.

Cortex's lexical FTS5 tier is complemented by a semantic tier: each page body is
embedded into a float32 vector, stored as a BLOB in SQLite, and compared to the
query embedding by cosine similarity. For a page-level KB (hundreds to low
thousands of pages) an exact in-memory dot-product scan is fast (<10ms) and has
zero native-extension dependencies, which keeps fleet deployment trivial. If the
store ever grows to tens of thousands of chunks, swap `_cosine_scan` for a
sqlite-vec `vec0` virtual table behind the same interface.

The embedding service is any OpenAI-compatible `/v1/embeddings` endpoint. The
base URL/model strings are private per-profile config — point at a local model
server, a hosted embeddings API, or a gateway without changing this code.

Config precedence (highest first):
  1. Explicit constructor args
  2. Env: CORTEX_EMBED_URL / CORTEX_EMBED_MODEL / CORTEX_EMBED_KEY / CORTEX_EMBED_DIM
  3. Built-in defaults (endpoint blank ⇒ semantic tier off, lexical-only)
"""

from __future__ import annotations

import json
import logging
import math
import os
import struct
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Built-in defaults — endpoint intentionally EMPTY so no infrastructure address
# ever lands in source control. The embedding endpoint is private config: set it
# per profile via plugins.cortex.embed_url (config.yaml) or the CORTEX_EMBED_URL
# env var. With no URL configured the semantic tier stays off and Cortex runs
# lexical-only FTS5 — a safe default that never breaks recall.
DEFAULT_EMBED_URL = ""
DEFAULT_EMBED_MODEL = "text-embedding-embeddinggemma-300m-qat"
DEFAULT_EMBED_DIM = 768


def pack_vector(vec: list[float]) -> bytes:
    """Serialize a float vector to a compact little-endian float32 BLOB."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Inverse of pack_vector."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def normalize(vec: list[float]) -> list[float]:
    """L2-normalize so cosine similarity reduces to a dot product."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Assumes inputs may be unnormalized."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class OpenAIEmbeddingClient:
    """Minimal OpenAI-compatible embeddings client (stdlib only).

    Batches inputs, retries transient failures, and returns L2-normalized
    vectors so downstream cosine math is a plain dot product.
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        dimensions: int | None = None,
        timeout: float = 30.0,
        batch_size: int = 32,
        max_retries: int = 3,
    ) -> None:
        self.url = url or os.environ.get("CORTEX_EMBED_URL") or DEFAULT_EMBED_URL
        self.model = model or os.environ.get("CORTEX_EMBED_MODEL") or DEFAULT_EMBED_MODEL
        self.api_key = api_key or os.environ.get("CORTEX_EMBED_KEY") or ""
        env_dim = os.environ.get("CORTEX_EMBED_DIM")
        self.dimensions = dimensions or (int(env_dim) if env_dim else DEFAULT_EMBED_DIM)
        self.timeout = timeout
        self.batch_size = batch_size
        self.max_retries = max_retries

    def _post(self, inputs: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": inputs}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                items = sorted(data["data"], key=lambda d: d.get("index", 0))
                return [list(it["embedding"]) for it in items]
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, TimeoutError) as e:
                last_err = e
                logger.debug("CortexEmbed: attempt %d/%d failed: %s", attempt, self.max_retries, e)
        raise RuntimeError(f"embedding request failed after {self.max_retries} attempts: {last_err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts. Returns L2-normalized vectors, one per input."""
        if not texts:
            return []
        if not self.url:
            raise RuntimeError("no embedding endpoint configured (set embed_url / CORTEX_EMBED_URL)")
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            # Guard against pathologically long inputs (model context ~2048 tok).
            batch = [t[:8000] if t else " " for t in batch]
            vecs = self._post(batch)
            out.extend(normalize(v) for v in vecs)
        return out

    def health(self) -> bool:
        """Return True if the endpoint answers a tiny probe embed."""
        try:
            v = self.embed(["ping"])
            return bool(v) and len(v[0]) == self.dimensions
        except Exception as e:
            logger.debug("CortexEmbed: health probe failed: %s", e)
            return False
