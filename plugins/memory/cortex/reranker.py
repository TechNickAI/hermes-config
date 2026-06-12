"""Reranker client for Cortex hybrid retrieval.

After the lexical (FTS5/BM25) and semantic (vector) tiers are fused via Reciprocal
Rank Fusion, the top candidates are *related* to the query but not necessarily
ordered by how directly they ANSWER it. A cross-encoder reranker reads each
(query, document) pair jointly and emits a relevance score, pulling the most
answer-bearing page to the top before the limited prefetch budget truncates the
list.

The reranker speaks the Cohere `/v1/rerank` request/response shape (the de-facto
standard, also spoken by Jina, Voyage, Together, and llama.cpp's `--reranking`
server). Point it at any compatible endpoint — a local cross-encoder, a router,
or a hosted API — without changing this code.

Design contract: **fail-safe, never throws into the hot path.** Any error
(endpoint down, timeout, malformed response) returns None so the caller keeps the
existing RRF order. A broken or unreachable reranker degrades retrieval to "good
hybrid search," never to "broken search."

Config precedence (highest first):
  1. Explicit constructor args
  2. Env: CORTEX_RERANK_URL / CORTEX_RERANK_MODEL / CORTEX_RERANK_KEY
  3. Built-in defaults (endpoint blank ⇒ reranking off, RRF order preserved)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Built-in defaults — endpoint intentionally EMPTY so no infrastructure address
# ever lands in source control. The rerank endpoint is private config: set it per
# profile via plugins.cortex.rerank_url (config.yaml) or the CORTEX_RERANK_URL env
# var. With no URL configured, reranking stays off and Cortex returns the RRF
# fusion order unchanged — a safe default that never breaks recall.
DEFAULT_RERANK_URL = ""
DEFAULT_RERANK_MODEL = "bge-reranker-v2-m3"


class CortexReranker:
    """Minimal Cohere-compatible `/v1/rerank` client (stdlib only).

    Reorders a candidate list by cross-encoder relevance. Fail-safe: `rerank`
    returns None on any failure so callers preserve their input order.
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.url = url or os.environ.get("CORTEX_RERANK_URL") or DEFAULT_RERANK_URL
        self.model = model or os.environ.get("CORTEX_RERANK_MODEL") or DEFAULT_RERANK_MODEL
        self.api_key = api_key or os.environ.get("CORTEX_RERANK_KEY") or ""
        self.timeout = timeout

    def _post(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        payload = json.dumps(
            {"model": self.model, "query": query, "documents": documents, "top_n": top_n}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Cohere shape: {"results": [{"index": int, "relevance_score": float}, ...]}
        results = data.get("results")
        if not isinstance(results, list):
            raise ValueError("rerank response missing 'results' array")
        return results

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[int] | None:
        """Return candidate indices reordered best-first, or None on failure.

        The returned list is a permutation of (a prefix of) range(len(documents)):
        index `i` in the result means `documents[i]` from the input. Indices not
        mentioned by the reranker are appended in their original order so no
        candidate is silently dropped.
        """
        if not self.url or not query or not documents:
            return None
        n = top_n or len(documents)
        try:
            results = self._post(query, documents, n)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError) as e:
            logger.debug("CortexReranker: rerank failed (%s) — preserving input order", e)
            return None
        except Exception as e:  # defensive: never let rerank break the hot path
            logger.debug("CortexReranker: unexpected rerank error (%s) — preserving order", e)
            return None

        order: list[int] = []
        seen: set[int] = set()
        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(documents) and idx not in seen:
                order.append(idx)
                seen.add(idx)
        if not order:
            return None
        # Safety net: append any candidate the reranker didn't mention.
        for i in range(len(documents)):
            if i not in seen:
                order.append(i)
        return order

    def health(self) -> bool:
        """Return True if the endpoint answers a tiny probe rerank."""
        if not self.url:
            return False
        try:
            order = self.rerank("ping", ["alpha", "beta"], top_n=2)
            return order is not None
        except Exception as e:
            logger.debug("CortexReranker: health probe failed: %s", e)
            return False
