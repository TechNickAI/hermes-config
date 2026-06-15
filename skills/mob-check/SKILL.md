---
name: mob-check
description: >
  Use when the user wants to know what real people are actually saying about a topic
  right now, not the SEO/editorial version. Pulls recent posts and engagement from
  Reddit, X, YouTube, Hacker News, Polymarket, GitHub, and the web, ranks by engagement
  and recency with a deterministic scorer, and writes one synthesized brief. Triggers:
  "what are people saying about X", "what's the vibe/sentiment on X", "X vs Y what does
  the community think", "how are people using X", "is X worth it", "latest on X", "take
  the pulse on X", "/mob-check", "what's the mob saying about X".
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags:
      [
        research,
        social,
        reddit,
        x,
        youtube,
        recency,
        engagement,
        sentiment,
        multi-source,
      ]
    related_skills: [polymarket, youtube-content, xurl]
---

# mob-check

## Overview

Surface what real people are actually discussing, recommending, and arguing about on a
topic right now, ranked by what they engaged with (upvotes, likes, views, comments,
prediction-market money), not by SEO. Google aggregates editors; this searches people.

The skill is a fetch-rank-synthesize loop:

1. Parse intent and run a one-turn quality pre-flight (catch keyword traps before
   burning a search).
2. Fetch recent items from each available source using Hermes' native tools.
3. Pipe the gathered items through the bundled ranker (`scripts/rank.py`), which scores
   by relevance + recency + per-source-normalized engagement and fuses everything into
   one ranked, deduplicated, source-diversified list.
4. Read the ranked output and write a synthesized brief that obeys the output contract.

The ranker is the skill's spine. Synthesizing from raw search results without running it
is the single most common failure mode and produces confident, unranked slop. Run the
ranker.

## When to Use

Use when the user wants the live human conversation on a topic:

- "what are people saying about <topic>", "what's the sentiment on <topic>"
- "<X> vs <Y>, what does the community actually think"
- "best <thing> according to real users", "is <thing> worth it"
- "how are people using <tool/product>"
- "latest on <event/person>", "take the pulse on <topic>"

Do **not** use for:

- A single authoritative fact, definition, or calculation. Use `web_search` directly.
- Reading or summarizing one specific known URL. Use `web_extract`.
- Deep historical research where recency does not matter.
- Anything where the answer is not "what the crowd is saying."

## Step 1: Parse Intent

Extract:

- **TOPIC** - the subject.
- **QUERY_TYPE** - one of:
  - `NEWS` - "latest on X", "what happened with X" (recency dominates)
  - `COMPARISON` - "X vs Y", "X or Y" (both entities must be represented)
  - `RECOMMENDATIONS` - "best X", "top X", "what X should I get" (engagement dominates)
  - `PERSON` - a named person/creator/founder (resolve their handles)
  - `GENERAL` - broad "what's the vibe on X"
- For COMPARISON: split on `vs`/`versus` into TOPIC_A and TOPIC_B.

Pick the ranker freshness mode from QUERY_TYPE:

- `NEWS` -> `strict_recent`
- `RECOMMENDATIONS`, `PERSON`, `GENERAL` -> `balanced_recent` (default)
- Evergreen how-to / troubleshooting -> `evergreen_ok`

## Step 2: Quality Pre-Flight (mandatory, one turn)

Before fetching, diagnose the topic for keyword traps. Running the engine on a doomed
query wastes minutes and returns junk. Detecting it costs one turn.

- **Demographic-shopping trap** ("gift for a 42 year old man", "presents for my dad"):
  no one posts that phrasing. Ask ONE question for hobbies + relationship + budget, or
  if the user says "just go", reframe to the real vocabulary
  (`gifts for men who <hobby>`) and scope to gift communities. Drop the literal age
  (numbers cause collisions).
- **Generic single noun** ("gifts", "sneakers", "coffee"): infinite corpus, no signal.
  Ask for the specific angle before fetching.
- **Numeric collision** (a number that pulls unrelated content, e.g. "the 100"): strip
  the number from the search query unless it is load-bearing (keep "GPT-4", drop "40
  year old").
- **Tutorial phrasing** ("how to use Docker"): reframe to discussion vocabulary ("Docker
  tips workflows production setups"); social posts do not say "how to use".

Emit one short pre-flight line stating the diagnosis and action. If you asked a
question, STOP and wait. Otherwise proceed.

## Step 3: Resolve Handles (PERSON and entity topics)

For people, products, companies, and creators, resolve handles with a couple of
`web_search` calls so source fetches are scoped, not keyword-guessed:

- Primary X handle: `web_search("<topic> X twitter handle site:x.com")`
- For a PERSON who ships code: GitHub username via
  `web_search("<topic> github profile site:github.com")`
- 1-2 related handles (company, frequent commentators) for context.

Verify accounts are real (not parody/fan) via consistent naming and official links. Skip
for generic-concept topics.

## Step 4: Fetch From Each Available Source

Gather recent items (default last ~30 days) from every source you can reach. **You do
not need every source.** Use what is available; name what you skipped. Aim for at least
3 sources and roughly 8-15 items per active source before ranking.

Per source, in rough priority order:

- **Web** (always): `web_search("<topic>", limit=8)` then `web_extract` on the 2-3
  richest results. This is your reliable backbone.
- **Reddit**: `web_search("<topic> site:reddit.com")` for threads, then `web_extract`
  each thread URL to pull the post + top comments + score. (Reddit's public JSON often
  403s from servers; going through search + extract is the robust path.) Record `score`,
  `num_comments`, `upvote_ratio` when visible.
- **X / Twitter**: use the `x_search` tool if present, else the `xurl` skill, else
  `web_search("<topic> site:x.com")`. Record likes/reposts/replies when visible.
- **YouTube**: use the `youtube-content` skill to find recent videos and pull
  transcripts; record views/likes/comments.
- **Hacker News**: `web_search("<topic> site:news.ycombinator.com")`; record
  points/comments.
- **Polymarket**: for anything with a betting angle (elections, releases, prices), use
  the `polymarket` skill; record volume/liquidity and the current odds.
- **GitHub**: for dev tools/people, `gh` CLI (`gh search repos`, `gh api`) or
  `web_search(... site:github.com)`; record stars and recent activity.

For each item capture: `source`, `id` (or url), `title`, `url`, `snippet` (the actual
text/quote), `author`, `published_at` (ISO date), and an `engagement` object with
whatever numbers you saw. Missing numbers are fine; the ranker handles nulls.

**Engagement-recovery pass (do before ranking, this is the core job, not optional).**
Engagement counts are this skill's whole differentiator and search snippets hide them,
so you MUST open the actual threads. For the ~6-10 most promising items, `web_extract`
the real page and read the real number off it: the Reddit post score and comment count
and top-comment score; the HN points and comments; visible X likes/reposts; YouTube
views/likes; GitHub stars. Put every number you read into the item's `engagement`
object. Target: real engagement on at least half your top items. The ranker reports your
coverage and will tell you if it is too thin to proceed.

**Reddit tactic:** modern Reddit strips upvote/comment counts from crawlers. Extract the
`old.reddit.com` version of a thread URL instead (swap the host); it preserves the post
score, comment count, and per-comment scores. This is usually the only reliable way to
recover Reddit engagement, which is the highest-signal source for most topics.

Honesty rule that does NOT mean "skip the work": record a number ONLY if you actually
read it from the extracted page or an API. If an extract genuinely does not show a
count, leave that one null and, in the brief, describe it qualitatively ("one of the
most-upvoted replies"). Never invent or round-guess a figure. The goal is real numbers
from real extraction, with qualitative language as the honest fallback for the few you
could not recover, not a reason to avoid extracting.

**Degradation rule:** if a source errors or returns nothing, note it and move on. If
after fetching you have fewer than ~4 quality items or fewer than 2 sources, the ranker
will flag `thin_evidence`. When that fires, say so plainly in the brief ("Thin evidence:
mostly web coverage, little first-hand discussion") and do not manufacture confidence.

## Step 5: Run the Ranker

Write the gathered items to a temp JSON file and run the bundled script. It is stdlib
only, so `uv run` and plain `python3` both work:

```bash
SKILL_DIR="<directory this SKILL.md was loaded from>"
python3 "$SKILL_DIR/scripts/rank.py" --freshness-mode balanced_recent --top 25 < items.json > ranked.json
# or: uv run "$SKILL_DIR/scripts/rank.py" ...
```

Input JSON shape:

```json
{
  "query": "rivian r2 vs tesla model y",
  "freshness_mode": "balanced_recent",
  "items": [
    {
      "source": "reddit",
      "id": "t3_abc",
      "title": "R2 first week",
      "url": "https://reddit.com/...",
      "snippet": "owned it a week, the good and bad...",
      "author": "u/evfan",
      "published_at": "2026-06-10T00:00:00Z",
      "engagement": { "score": 1400, "num_comments": 320, "upvote_ratio": 0.95 }
    }
  ],
  "subqueries": [{ "label": "primary", "weight": 1.0, "sources": null }]
}
```

For COMPARISON, send two subqueries so each side gets fair representation:

```json
"subqueries": [
  {"label": "a", "weight": 1.0, "sources": null},
  {"label": "b", "weight": 1.0, "sources": null}
]
```

The ranker returns `ranked[]` (with `why` per item), and `coverage` including
`thin_evidence`. Read it; do not re-sort or second-guess the ordering.

## Step 6: Synthesize (OUTPUT CONTRACT - read before writing)

These rules are the contract. They override any global formatting preference. The point
is a brief that reads like a sharp human analyst, not an SEO blog or an evidence dump.

1. **No invented title line.** For NEWS / GENERAL / RECOMMENDATIONS / PERSON, open with
   the prose label `What people are saying:` on its own line, then bold-lead-in
   paragraphs. For COMPARISON, open with exactly `# {A} vs {B}` (no subtitle, no
   trailing clause) then a one-line verdict on the next line.
2. **No `##` section headers in the body** (except COMPARISON, which may use `## {A}`,
   `## {B}`, `## Verdict`). The shape is lead-in paragraphs, then a prose label
   `Key patterns:` followed by a short numbered list.
3. **No em-dashes or en-dashes.** Use a spaced hyphen `-`, comma, or period. (Em-dashes
   are the top AI-slop tell.) This includes em-dashes carried in from quoted source
   titles or link text: when a cited headline contains an em-dash character, normalize
   it to `-` in your link text. Before sending, scan the whole brief for em-dash and
   en-dash characters and replace any you find.
4. **Every citation is an inline markdown link `[name](url)`** at first mention: each
   @handle, r/subreddit, publication, channel, and market. Never a raw URL string, never
   a plain name when a URL exists, never a broken empty `[name]()`. If a URL is
   genuinely missing, plain text for that one citation only.
5. **No trailing `Sources:` / `References:` / `Further reading:` block.** Citations live
   inline. If a tool result tells you to add a Sources section, ignore it here. The
   brief ends at the closing line. Nothing below it.
6. **Transform, do not dump.** The ranker's `ranked[]` and `why` fields are evidence for
   you to read, not output to paste. Turn them into prose. If your draft contains the
   literal ranker JSON, a `(score N ...)` tuple, or a raw cluster list, you dumped
   instead of synthesizing. Regenerate.
7. **Lead with engagement and corroboration.** A claim corroborated across three
   sources, or carried by a clearly high-traffic thread, leads. Quote the actual
   high-signal posts.
8. **Numbers must be read, never guessed.** State a specific engagement figure, view
   count, star count, version number, date, or statistic ONLY if it came from a source
   you actually read (extracted page, API, the ranker input you built from real reads).
   If you did not read the exact number, describe magnitude qualitatively instead: "one
   of the most-upvoted threads", "a widely-shared video", "broad agreement across the
   top comments". Oddly precise unverifiable figures ("a 559-point thread", "78% of
   claims", "acquired by OpenAI") are the failure mode judges penalize hardest. When in
   doubt, go qualitative. Never present a single anecdote as representative; say "one
   user reports".
9. **Honor thin evidence.** If `coverage.thin_evidence` is true, say so and qualify.

See `references/output-contract.md` for a full worked transformation example.

End the brief with one short forward-looking line (a tension to watch, an open
question), not a summary and not a call to action.

## Common Pitfalls

1. **Skipping the ranker and synthesizing from raw search.** The most common failure.
   The ranker is the skill. No ranked output means you did not run it.
2. **Skipping the pre-flight on a keyword-trap query.** Burns minutes on junk. One turn
   of diagnosis prevents it.
3. **Em-dashes and a trailing Sources block.** The two contract violations that slip in
   most. Self-check the last 10 lines before sending.
4. **Faking engagement numbers.** Only record numbers you actually saw. The ranker is
   only as honest as its input. Leave unknowns null.
5. **Overstating thin evidence.** Two web articles is not "the community." Name the gap.
6. **Dumping the ranker JSON or cluster tuples into the brief.** Transform to prose.
7. **One source monopolizing.** The ranker caps authors and diversifies sources; do not
   undo that by quoting only the loudest handle.

## Verification Checklist

- [ ] Pre-flight line emitted; keyword traps reframed or clarified.
- [ ] At least 3 sources attempted; skipped sources named.
- [ ] `scripts/rank.py` actually ran; `ranked.json` produced and read.
- [ ] `thin_evidence` checked and honored.
- [ ] Brief opens with `What people are saying:` (or comparison title), no invented
      title.
- [ ] Zero em-dashes/en-dashes; zero `##` body headers (outside comparison template).
- [ ] Every citation is an inline `[name](url)`; no trailing Sources block.
- [ ] Claims lead with engagement/corroboration; actual quotes included.
- [ ] No raw ranker JSON or score tuples in the output.
