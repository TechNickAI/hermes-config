---
name: moa-solve
description: >
  Use when you have a HARD, open-ended, high-stakes problem worth throwing multiple AI
  models at and pulling the best solution out — architecture decisions, strategy design,
  thorny debugging, research synthesis, "what am I missing", tool/system design. This is
  the SOLVE counterpart to multi-review (which critiques an existing artifact). It fans
  the problem out to a role-differentiated panel of model families (proposer layer),
  then the caller acts as aggregator and builds a NEW best-of-breed answer via a
  component ledger — not an average, not a cut-and-paste splice. Also encodes WHEN NOT
  to use a panel (most problems), routing to a single strong model or the commodity
  openrouter/fusion instead. Accumulates a persistent model-task-fit knowledge base so
  future runs pick the right models for the right roles.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [orchestration, multi-model, moa, synthesis, problem-solving, fusion]
    related_skills: [multi-review, smart-delegation, create-great-prompts]
---

# MOA-Solve — Mixture-of-Agents for hard problems

## What this is

A control plane for solving hard problems with multiple models. Grounded in
Mixture-of-Agents (Wang et al., Together AI, arXiv:2406.04692, ICLR 2025): a **proposer
layer** (many model families answer independently) plus an **aggregator** (one strong
model — usually YOU — writes a NEW synthesized answer). The paper's load-bearing result:
the aggregator that _synthesizes_ beats an LLM-ranker that merely _picks the best
proposal_. That is the empirical basis for "pull the best from EACH, don't pick the
single best ONE."

Important reframing (do not skip): "best from each" does NOT mean cut-and-paste splicing
spans from proposers. Splicing produces Frankenstein artifacts where model A's
architecture assumes state that model B's grafted completion never provides. It means:
**synthesize a superior answer that is accountable to the strongest element found in
each proposal**, grafted through explicit interface checks. The component ledger (below)
is how you do that without averaging to mush.

## When to use (and when NOT to — read this first)

The most common failure is running a panel when you shouldn't: you pay 4-6x the cost and
latency for consensus noise on a problem where a single model was fine. Panels earn
their keep only when models _disagree_, and disagreement is rare on easy problems.

Routing rule:

- **Single strong model** — routine, low-stakes, or easily verifiable.
- **`openrouter/fusion`** (commodity MoA, cheap, benchmark-competitive) — the problem is
  genuinely hard/open-ended but generic model diversity is enough and you don't need a
  special role or tool. Call it as a model (`model: "openrouter/fusion"`) or as a tool
  (`tools:[{"type":"openrouter:fusion"}]`) so YOU write the final answer. This is the
  DEFAULT for hard pure-reasoning problems.
- **Custom `moa-solve` panel (this skill)** — only when ALL hold: (a) high-stakes /
  irreversible / reusable, (b) genuinely open-ended (multiple defensible answers, no
  ground-truth), AND (c) it benefits from a role or tool `openrouter/fusion` can't route
  (a red-teamer with a shell, Grok with live Twitter, a formal solver,
  provenance/auditability of every proposal).

Do NOT route on fusion's vendor benchmark claims — they're unverified and
task-dependent. Route on task SHAPE.

Trigger phrases: "throw a few models at this", "hard problem, want the best solution",
"what am I missing on X", "design/architect Y", "solve this properly", "MOA this",
"panel this".

## The models (resolve live, never hardcode stale slugs)

- Proposers via OpenRouter (families, live-resolved to current flagships by the runner):
  Grok (`x-ai/grok-*`, has Twitter), Gemini Pro, GPT, DeepSeek. Plus
  `openrouter/fusion`.
- Custom router backends supported via `MOA_OMNI_BASE_URL` env var (OpenAI-compatible
  shape, must send `stream:false`).
- **You (the calling agent) are the aggregator of record. Do NOT seat yourself as a
  proposer** — it double-counts and biases synthesis.

## Panel design for a hard problem (2 generalists + 3 specialists)

Shared core prompt for every seat, plus a limited role OVERLAY on the specialists. Every
seat — including specialists — must return a COMPLETE proposed solution. Roles alter
optimization priority, not scope. This resolves the same-prompt-vs-role-differentiated
tension: 2 unconstrained generalist seats protect raw solution quality; 3 differentiated
seats supply formalism, robustness, and novelty; nobody is allowed to skip solving the
problem (prevents "role soup" fragments).

Default lineup:

1. **Generalist A** (GPT) — shared prompt only, strongest end-to-end answer.
2. **Generalist B** (Gemini) — shared prompt only, genuinely independent architecture.
3. **Formal/constraint solver** (DeepSeek R1) — assumptions, invariants, calculations,
   "what would make this invalid".
4. **Robustness / red-team** (Grok) — full solution optimized for edge cases +
   adversarial conditions; leads with the sharpest attacks and cheapest kill-tests.
5. **Creative contrarian** (DeepSeek or a second Grok temp) — non-obvious, novel levers
   others miss.

Round 1 is independent — do NOT show proposals to each other. Use targeted
cross-examination only after you've identified contradictions.

## Orchestrator steps (you, in order)

1. **GATE** — is this panel-worthy per the routing rule? If not, single model or fusion.
   Set a budget/latency cap.
2. **PRESERVE** the raw problem verbatim in the run dir.
3. **SHARPEN** into: goal, constraints, deliverable format, success tests, non-goals.
4. **DEFINE "GOOD"** — attach the scoring rubric + concrete acceptance tests BEFORE
   seeing answers.
5. **SELECT PANEL** — query the fitlog KB (`scripts/fitlog.py report --kind <class>`)
   for which model wins which role on this task class; keep some exploration diversity.
6. **FAN OUT** — write a `seats.json`, run `scripts/panel.py`. Independent, parallel.
7. **NORMALIZE** each answer into: assumptions / architecture / steps / risks / tests /
   novel ideas.
8. **VALIDATE** objective claims yourself (run code, calcs, searches). Model agreement
   is NOT validation — it can be correlated hallucination.
9. **SCORE** whole answers + individual components on the rubric (anonymize model
   identity while scoring to kill brand bias). Log via `scripts/fitlog.py score ...`.
10. **BUILD THE LEDGER** (the core move) — a requirement matrix, candidate components,
    disposition per contribution. See `templates/synthesis-ledger.md`. Pick ONE
    architectural spine (highest- scoring architecture, not an average). Graft
    compatible components through interface checks. Quarantine speculative ideas as
    labeled "optional experiments".
11. **SYNTHESIZE** a NEW answer accountable to the strongest element of each proposal.
    Attribute origin inline (`[GPT arch]`, `[R1 edges]`, `[Grok option B]`) for trust.
12. **ADVERSARIALLY TEST** the synthesized answer itself. One repair pass max.
    **Anti-mush fallback: diff the synthesis against the best single proposal; if the
    synthesis is longer but not measurably higher on the rubric, SHIP THE BEST SINGLE
    PROPOSAL.**
13. **PERSIST** the run + scores + (later) the real-world outcome. Update the KB.

## Scoring rubric (define "good" for a SOLUTION)

0-5 integers each. Explicitly BAN: number of sources cited, length, confidence-theater,
"sounds smart".

| Dim                | 0                           | 3                         | 5                                                       |
| ------------------ | --------------------------- | ------------------------- | ------------------------------------------------------- |
| **Completeness**   | misses core requirements    | main path, weak edges     | all requirements + material edge cases + constraints    |
| **Soundness**      | wrong / unsafe / incoherent | plausible with gaps       | internally consistent, constraint-satisfying, checkable |
| **Actionability**  | essay, unusable             | partial plan              | operator can execute without rework                     |
| **Usable-Novelty** | generic rehash              | 1 usable non-obvious idea | multiple usable new levers (not sci-fi)                 |
| **Testability**    | no way to verify            | partial checks            | clear acceptance tests / numbers / code                 |

Hard gates first: anything that violates a mandatory constraint or is unsafe is
disqualified regardless of score. The KB stores per (model x role x task-class) rolling
means plus a free-text `won_on` contribution note. Require >= 5 samples before
overweighting a seat; decay old scores.

## The tools (in this skill's scripts/)

- `scripts/panel.py --brief B.md --seats S.json --out DIR --label NAME` — live-resolves
  OpenRouter flagships, dispatches all seats in parallel, writes each response
  incrementally + a manifest. stdlib only; reads keys from the profile `.env` (override
  with `MOA_ENV_PATH`). `seats.json` is a list of `{seat, model, backend, mandate}`;
  `model` may be a family shorthand (`grok`/`gemini`/`gpt`/`deepseek`) that resolves to
  the live flagship, or an explicit slug (`openrouter/fusion`). `backend` is
  `openrouter` or `omniroute`.
- `scripts/fitlog.py init | add-run | score | report [--kind CLASS]` — the persistent
  model-task-fit SQLite KB. Query it in step 5, write to it in step 9. Portable via
  `MOA_FITLOG_DB` env var.
- `templates/synthesis-ledger.md` — the component-ledger + requirement-matrix template
  for step 10.

## Pitfalls

1. **Slow reasoning models (DeepSeek R1, `openrouter/fusion`) can take 2-7 minutes.**
   `panel.py` writes each seat file and updates the manifest incrementally as seats
   complete, so partial results are visible during long runs. Still run serious panels
   BACKGROUNDED and poll the manifest rather than relying on a short foreground timeout.
2. **OmniRoute custom router needs `stream:false`** or you get SSE chunks that fail
   `json.loads` (already handled in panel.py; remember it if you write a fresh caller).
3. **`openrouter/fusion` returns a HUGE payload** (~70KB — it dumps every panel member +
   judge rationale). Grep its section headers, don't read it whole into context.
4. **Grok ONLY via OpenRouter** (`x-ai/grok-*`), never via native xAI endpoints if your
   config prohibits it.
5. **Correlated hallucination masquerades as consensus.** All the models share training
   blind spots; if 4 agree, that raises confidence ONLY if their error sources are
   plausibly independent. Never report "N models agreed" as if agreement-count were
   quality. Validate against primary sources.
6. **Don't seat the aggregator as a proposer.** You bias your own synthesis toward your
   own draft.
7. **`seats.json` model names must be a family shorthand, a full OpenRouter slug, or a
   close variant `panel.py` can fuzzy-match back to a family** (fixed 2026-07: prior
   versions did an exact-string lookup only, so near-miss guesses like `grok-4-5` or
   `gemini-3-pro-preview` silently fell through as literal (bogus) OpenRouter model IDs
   and 400/404'd). `resolve_model_alias()` now normalizes and prefix-matches against the
   4 known families (`grok`/`gemini`/`gpt`/`deepseek`), live-resolving to the CURRENT
   flagship rather than trusting a caller's guessed version number. A slug containing
   `/` is always passed through untouched (explicit choice). Anything that matches no
   family logs a `[alias] WARNING` to stderr and falls back to the first family default
   instead of silently erroring the whole seat — check stderr/the manifest if a seat's
   model looks unexpected.

## Verification checklist

- [ ] Passed the GATE (or honestly routed to single-model / fusion instead)
- [ ] Raw problem preserved; sharpened brief written; "good" defined before answers seen
- [ ] At least 3 model FAMILIES actually ran (else stamp `degraded: single-family`)
- [ ] Each proposal scored + logged to fitlog
- [ ] Component ledger built; ONE spine chosen; contributions have dispositions +
      attribution
- [ ] Synthesis adversarially tested; anti-mush fallback checked (ship best-single if
      not better)
- [ ] Run + scores persisted; KB updated
