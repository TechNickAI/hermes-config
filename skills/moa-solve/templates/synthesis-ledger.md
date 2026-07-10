# Synthesis Ledger — {PROBLEM TITLE}

_Aggregator: {you} · Panel: {seats} · Date: {date} · Run dir: {path}_

The ledger is how you "pull the best from each" WITHOUT averaging to mush or
Frankenstein-splicing. Fill it BEFORE you write the final synthesis. Forcing explicit
attribution + disposition in the scratchpad makes it mathematically hard to blend the
answer into a bland average.

## 1. Requirement matrix

List every hard requirement / success test from the sharpened brief. This is what
"complete" means.

| #   | Requirement / success test | Mandatory? |
| --- | -------------------------- | ---------- |
| R1  | ...                        | yes        |
| R2  | ...                        | yes        |
| R3  | ...                        | preference |

## 2. Candidate components (atomized from proposals)

Break each proposal into discrete, tagged components. One row per distinct
idea/mechanism. Score each on the rubric dims that matter for THAT component (not the
whole proposal).

| ID  | Component | Source seat | Type (architecture / step / edge-case / novel / risk) | Local fitness | Satisfies |
| --- | --------- | ----------- | ----------------------------------------------------- | ------------- | --------- |
| C1  | ...       | GPT         | architecture                                          | 5             | R1,R2     |
| C2  | ...       | DeepSeek    | novel                                                 | 4             | R3        |
| C3  | ...       | Grok        | edge-case                                             | 5             | R2        |

## 3. Spine selection

Choose ONE architectural spine — the highest-scoring _architecture_ component, not an
average of several. State it and why. Every other component gets grafted onto THIS spine
or rejected.

> Spine: **C{n}** ({source}) — {one line why}.

## 4. Disposition log (every distinctive contribution gets exactly one)

| Component | Disposition           | Reason / interface check                          |
| --------- | --------------------- | ------------------------------------------------- |
| C1        | ADOPTED (spine)       | ...                                               |
| C2        | ADAPTED               | reframed to fit spine's assumptions               |
| C3        | ADOPTED               | grafts cleanly; converted into acceptance test T2 |
| C4        | REJECTED              | conflicts with spine's state model; less sound    |
| C5        | DEFERRED (experiment) | valuable but unverified; needs test X before core |

Rules:

- Never hide a contradiction with vague compromise language. Pick one side, say why,
  quarantine the rest.
- Graft only components whose assumptions/units/interfaces/security-boundaries are
  compatible with the spine. Verify each graft.
- Speculative-but-valuable ideas go to "optional experiments" with a validation
  criterion — they do NOT silently enter the core design.

## 5. Contradictions surfaced (do not average these away)

- {topic}: {seat A position} vs {seat B position} → **resolved by {test / criterion}** →
  chose {X}.

## 6. Anti-mush check (MANDATORY before shipping)

- Best single proposal: **{seat}**, rubric total {n}/25.
- Synthesis rubric total: {n}/25.
- Is the synthesis measurably better (higher rubric, satisfies more requirements)? yes /
  no
- If NO → **ship the best single proposal instead.** A longer blended answer that isn't
  better is a regression.

## 7. Final answer

Write the new synthesized solution here, in the brief's required deliverable format.
Attribute each major section's origin inline (`[GPT arch]`, `[R1 edges]`,
`[Grok option B]`). Then adversarially test THIS answer (not just its sources) and note
the result.
