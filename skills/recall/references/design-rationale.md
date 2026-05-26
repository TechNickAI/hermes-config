# Recall Skill — Design Rationale

## Why mission-driven, not prescriptive

The v0.1 recall skill was a step-by-step command reference. The agent hit one miss ("no
topic matching") and returned a dead end. The maintainer flagged it as a terrible
experience and asked for a rewrite.

The fix wasn't to add more steps — it was to change the framing entirely. The agent now
gets a **mission** (restore relevant context, never dead-end) and a **source cascade**
to work through. It figures out the path itself.

## The dead-end anti-pattern

A rigid flowchart fails exactly when recall is most needed — novel queries where the
prescribed path has no match. A phrase-based FTS miss ("alice relationship analysis") is
not an empty result; it's a signal to try something else:

- Break the phrase apart: `alice`, `relationship`, `analysis` separately
- Try `session_search()` with no query (recency fallback)
- Check memory and cortex for durable notes on the person/topic
- Fall through to tgcli raw history if nothing else works
- If everything is empty: **ask** what the user remembers, don't report failure

Every one of these is better than "no topic matching."

## The cascade rationale

Sources are ordered by richness and cost:

1. **session_search** — Hermes-native FTS, fast, covers agent actions + tool outputs
2. **Session transcripts** — full fidelity, but slow to load; only needed if search
   returns promising matches
3. **Memory / cortex** — durable facts the agent already distilled; cheap to check
4. **tgcli raw history** — user-visible chat surface only (no agent actions), covers
   pre-Hermes history or cross-bot context; on-demand sync required

## Lesson for future skill authoring

When designing a skill for a retrieval or search class of task:

- Lead with the _goal_, not the _steps_
- Give the agent sources + fallback order, not a decision tree
- Explicitly name the anti-pattern you're preventing (dead end, empty response)
- The synthesis step matters as much as the search: raw results ≠ context briefing
