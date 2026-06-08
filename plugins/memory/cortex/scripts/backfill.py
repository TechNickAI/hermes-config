#!/usr/bin/env python3
"""One-time (idempotent) Cortex embedding backfill for a single profile.

Run on each fleet host after the cortex plugin is updated. Embeds every page in
the profile's Cortex store that is missing or stale, using the OpenAI-compatible
endpoint from CORTEX_EMBED_URL (or --url). Safe to re-run: only missing/changed
pages are embedded.

Usage:
    python backfill.py --store /ABS/PATH/to/cortex
    # endpoint from env CORTEX_EMBED_URL / CORTEX_EMBED_MODEL / CORTEX_EMBED_DIM
    # or override:
    python backfill.py --store /ABS/PATH/to/cortex --url http://HOST:1234/v1/embeddings

Always pass an ABSOLUTE --store path. Some runtimes override $HOME, so "~" can
resolve to a shadow tree and point the store at the wrong (empty) directory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

# Make the plugin importable whether run from the plugin dir or elsewhere.
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from embeddings import OpenAIEmbeddingClient  # noqa: E402
from store import CortexStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill Cortex page embeddings.")
    ap.add_argument("--store", required=True, help="ABSOLUTE path to the cortex store dir")
    ap.add_argument("--url", default=None, help="embeddings endpoint (default: $CORTEX_EMBED_URL)")
    ap.add_argument("--model", default=None, help="model id (default: $CORTEX_EMBED_MODEL)")
    ap.add_argument("--dim", type=int, default=None, help="expected dimensions (default: $CORTEX_EMBED_DIM)")
    ap.add_argument("--force", action="store_true", help="re-embed every page even if unchanged")
    ap.add_argument("--no-backup", action="store_true", help="skip the .plugin.db backup")
    args = ap.parse_args()

    store_path = Path(args.store)
    if not store_path.is_absolute():
        print(f"ERROR: --store must be absolute (got {args.store!r})", file=sys.stderr)
        return 2
    if not store_path.is_dir():
        print(f"ERROR: store dir does not exist: {store_path}", file=sys.stderr)
        return 2

    md_count = sum(1 for _ in store_path.rglob("*.md"))
    print(f"store: {store_path}  (markdown files on disk: {md_count})")
    if md_count == 0:
        print("ERROR: 0 markdown files found — refusing to run against an empty store "
              "(wrong path? $HOME override?).", file=sys.stderr)
        return 2

    emb = OpenAIEmbeddingClient(url=args.url, model=args.model, dimensions=args.dim, timeout=60, batch_size=16)
    if not emb.url:
        print("ERROR: no endpoint. Set CORTEX_EMBED_URL or pass --url.", file=sys.stderr)
        return 2
    print(f"endpoint: {emb.url}  model: {emb.model}  dim: {emb.dimensions}")
    if not emb.health():
        print("ERROR: endpoint health check failed.", file=sys.stderr)
        return 3

    db = store_path / ".plugin.db"
    if db.exists() and not args.no_backup:
        bak = db.with_name(f".plugin.db.bak-embed-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(db, bak)
        print(f"backup: {bak.name}")

    st = CortexStore(store_path, embedder=emb)
    pages = st.count()
    print(f"pages indexed: {pages}")
    if pages < max(1, md_count - 20):
        print(f"ERROR: store sees only {pages} pages but {md_count} md files exist — "
              "aborting before backfill (index/path mismatch).", file=sys.stderr)
        return 4

    t0 = time.time()
    n = st.backfill_embeddings(force=args.force)
    dt = time.time() - t0
    stats = st.embedding_stats()
    print(f"embedded {n} pages in {dt:.1f}s ({n / max(dt, 0.001):.1f}/s)")
    print(f"coverage: {stats['embedded']}/{stats['pages']} pages  by_model={stats['by_model']}")
    if stats["embedded"] < stats["pages"]:
        print(f"WARNING: {stats['pages'] - stats['embedded']} pages still unembedded.", file=sys.stderr)
        return 5
    print("OK: full coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
