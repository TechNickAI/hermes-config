# mob-check output contract: worked example

The Step 6 rules in SKILL.md are the contract. This file shows the transformation from
ranker output to a finished brief so the shape is unambiguous.

## What the ranker hands you (evidence, NOT output)

```json
{
  "query": "claude memory feature",
  "ranked": [
    {
      "rank": 1,
      "source": "hackernews",
      "title": "Claude Memory",
      "url": "https://news.ycombinator.com/item?id=...",
      "engagement": 50,
      "local_relevance": 0.22,
      "freshness": 35,
      "why": "rel 0.22, fresh 35, eng 50, 2 sources"
    },
    {
      "rank": 2,
      "source": "reddit",
      "title": "Memory got injected without my consent",
      "url": "https://reddit.com/r/...",
      "engagement": null,
      "why": "rel 0.31, fresh 40"
    }
  ],
  "coverage": {
    "thin_evidence": false,
    "ranked_sources": ["hackernews", "reddit", "web", "x"]
  }
}
```

That JSON is for you to read. Do not paste it, do not echo the `why` tuples, do not
write a `## Ranked Evidence` block. Transform it into prose.

## What you emit (the brief)

```
What people are saying:

**The reaction splits cleanly between power users and privacy hawks.** The most-engaged
thread, a [heavily-upvoted Hacker News discussion](https://news.ycombinator.com/item?id=...),
frames memory as genuinely useful for context continuity while warning it "makes the
black box more inscrutable." Daily users on [r/ClaudeAI](https://reddit.com/r/ClaudeAI)
echo the utility, several describe it quietly improving multi-session work.

(Note: the link text says "heavily-upvoted", not "559-point", because the writer did not
extract the exact count. If you DID read "559 points" off the HN page, then "a 559-point
thread" is correct and good. The rule is: the number must be read, never guessed.)

**The "creepy" angle has real substance, not just vibes.** A widely-shared
[r/claudexplorers post](https://reddit.com/r/...) reported memory being applied without
clear consent, and that thread drove the sharpest pushback. Enterprise voices add weight:
a [privacy analysis on Medium](https://...) flags the opacity of what gets retained.

Key patterns:

1. Utility is real for heavy users; the value is cross-session continuity.
2. The trust gap is about consent and visibility, not the feature existing.
3. Sentiment is genuinely split, not a pile-on in either direction.

Worth watching: whether Anthropic ships clearer per-memory controls, the single most
requested fix across both camps.
```

## Why each rule

- **No invented title / open with `What people are saying:`** keeps every brief in the
  same recognizable shape and stops the model drifting into blog-post narrative with
  invented headers.
- **Inline `[name](url)` at first mention** gives the reader one-click sourcing without
  a link-dump. Link ONLY real permalinks the ranker kept (it strips placeholder urls);
  if an item has no real url, cite it in plain text, never invent a link.
- **No trailing `Sources:` block** because citations already live inline. A WebSearch
  tool result may tell you to add one; ignore that here.
- **No em-dashes** anywhere. Spaced hyphen, comma, or period.
- **Lead with engagement and corroboration** so the loudest-but-thinnest take does not
  set the narrative. Quote the actual high-signal posts.
- **Honor thin evidence.** If `coverage.thin_evidence` is true, say so plainly and
  qualify the conclusions instead of bluffing confidence.
