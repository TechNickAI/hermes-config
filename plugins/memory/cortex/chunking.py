"""Heading-aware markdown chunking for the Cortex semantic tier.

Why this exists
---------------
Page-level embeddings have two failure modes on a real knowledge base, both
measured on a live 1,206-page store:

1. **Truncation.** The embedding client caps input at 8,000 chars. 217 pages
   (18%) exceed that, discarding ~1.54M chars — 27% of all body text. A scan for
   globally-unique tokens found 111 of 114 large pages carry content that exists
   ONLY past the cutoff, so that text is unreachable by semantic search.
2. **Topic dilution.** A daily log covering six unrelated subjects averages into
   one vector that is weakly similar to all of them and strongly similar to
   none. Measured: a tail section about a fleet-update lesson did not appear in
   the semantic top-5 for a query naming it almost verbatim.

Chunking addresses both, and it is the higher-value half of the chunking
literature: Jina's LongEmbed evaluation measured ordinary 512-token chunking at
+24.47% relative nDCG@10 over unchunked long-document embeddings, versus roughly
+3% for the more elaborate "late chunking" technique.

Measured on this corpus (40 tail-only queries, each an author-written section
heading that is globally unique and absent from the page's first 8,000 chars,
scored through the full FTS+vector+rerank pipeline):

    recall@1  45% -> 60%
    recall@5  52% -> 60%
    MRR       0.467 -> 0.600
    latency   1424ms -> 1621ms median
    6 queries improved, 0 regressed, 34 unchanged

Note the honest framing: the lexical tier already rescued some tail content on
its own, so the gain is smaller than a vector-only comparison would suggest.

Design constraints
------------------
- **Chunk only what needs it.** Small pages keep exactly one page-level vector,
  so the common case is byte-for-byte unchanged and the index grows ~2x rather
  than ~6x. Vector count is a per-turn latency cost.
- **Split on structure, not byte offsets.** Markdown headings are real semantic
  boundaries an author already chose; fixed-width windows cut sentences in half.
- **Carry the heading path into the chunk text.** A chunk reading "he agreed to
  the change" is meaningless alone. Prefixing "Daily Log 2026-02-18 > Fleet
  Update Lesson" is a cheap, deterministic approximation of Anthropic's
  contextual-retrieval prefix, with no LLM call per chunk.
- **Never lose the tail.** Any oversized section is hard-split so no text is
  dropped, which is the whole defect being fixed.
"""

from __future__ import annotations

import re

# Chunk sizing. The target is a compromise: small enough that a chunk is about
# one topic, large enough that ~6 chunks cover a long page rather than ~40.
# Expressed in characters because the embedding limit is in characters.
DEFAULT_TARGET_CHARS = 3000
DEFAULT_MAX_CHARS = 6000

# Split before ATX headings h1-h3. Deeper headings (h4+) usually subdivide a
# single topic, so splitting on them fragments what should stay together.
_HEADING_SPLIT_RE = re.compile(r"(?m)^(?=#{1,3} )")
_HEADING_LINE_RE = re.compile(r"(?m)^(#{1,6})\s+(.*)$")


def _leading_heading(text: str) -> tuple[int, str] | None:
    """Return (level, title) if `text` opens with an ATX heading."""
    m = _HEADING_LINE_RE.match(text.lstrip("\n"))
    if not m:
        return None
    return len(m.group(1)), m.group(2).strip()


def _heading_trail(stack: list[tuple[int, str]]) -> str:
    return " > ".join(title for _, title in stack if title)


def split_markdown(
    body: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[tuple[str, str]]:
    """Split `body` into (heading_path, text) chunks.

    Sections are packed together until they reach `target_chars`, so a page of
    many small headings does not explode into a vector per heading. A single
    section larger than `max_chars` is hard-split on paragraph boundaries where
    possible, and mid-paragraph only as a last resort, because dropping the
    remainder is the exact bug this module fixes.

    Returns [] for empty input. Never returns a chunk of only whitespace.
    """
    if not body or not body.strip():
        return []

    sections = [s for s in _HEADING_SPLIT_RE.split(body) if s.strip()]
    if not sections:
        return []

    chunks: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    pending: list[str] = []
    pending_trail = ""

    def flush() -> None:
        nonlocal pending, pending_trail
        if pending:
            text = "".join(pending).strip()
            if text:
                chunks.append((pending_trail, text))
        pending = []

    for section in sections:
        head = _leading_heading(section)
        if head:
            level, title = head
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        trail = _heading_trail(stack)

        if len(section) > max_chars:
            flush()
            for piece in _hard_split(section, target_chars):
                chunks.append((trail, piece))
            pending_trail = trail
            continue

        current = sum(len(p) for p in pending)
        if pending and current + len(section) > target_chars:
            flush()
            pending_trail = trail
        elif not pending:
            pending_trail = trail
        pending.append(section)

    flush()
    return [(t, c) for t, c in chunks if c.strip()]


def _hard_split(text: str, target_chars: int) -> list[str]:
    """Break an oversized section into <=target_chars pieces, losing nothing."""
    paragraphs = text.split("\n\n")
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= target_chars:
            buf = candidate
            continue
        if buf:
            pieces.append(buf)
            buf = ""
        if len(para) <= target_chars:
            buf = para
            continue
        # A single paragraph over budget (tables, log dumps): slice it.
        for i in range(0, len(para), target_chars):
            slice_ = para[i : i + target_chars]
            if len(slice_) == target_chars:
                pieces.append(slice_)
            else:
                buf = slice_
    if buf.strip():
        pieces.append(buf)
    return [p for p in pieces if p.strip()]


def chunk_embedding_text(title: str, tags: str, heading_path: str, text: str) -> str:
    """Build the string actually embedded for a chunk.

    The page title and heading path are prepended so an excerpt carries its own
    context. This is the cheap, deterministic version of a contextual prefix:
    no LLM call, no per-chunk cost, and it survives re-indexing unchanged.
    """
    parts = [p for p in (title.strip() if title else "", heading_path.strip()) if p]
    header = " > ".join(parts)
    tag_line = f"Tags: {tags}" if tags else ""
    return "\n".join(p for p in (header, tag_line, "", text.strip()) if p is not None and p != "")
