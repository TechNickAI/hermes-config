#!/usr/bin/env python3
"""Render a nightly_doctor JSON result as a short human summary.

Lives in its own file rather than an inline `python -c` heredoc: quoting a
nested JSON-parsing snippet inside bash is exactly the kind of fragile plumbing
that silently falls back to dumping raw JSON at a human.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 1

    lines = []
    repairs = data.get("repairs") or []
    if repairs:
        lines.append("repairs: " + ", ".join(str(r) for r in repairs))

    emb = data.get("embeddings_after") or {}
    if emb:
        lines.append(f"embeddings: {emb.get('embedded', '?')}/{emb.get('pages', '?')}")

    retrieval = data.get("retrieval") or {}
    if retrieval:
        sources = ",".join(sorted(set(retrieval.get("sources") or []))) or "none"
        lines.append(f"retrieval: {retrieval.get('count', 0)} results via {sources}")

    backups = data.get("backups") or []
    lines.append(f"backup: {backups[0] if backups else 'none'}")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
