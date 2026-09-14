# Verifying a Verdict: Never Grade the Transcript

Use this when a reviewer (subagent, headless one-shot, or human) returns a verdict you
are about to act on, **especially** when two reviewers disagree, or when a PASS would
unlock acceptance, cutover, deployment, or an irreversible step.

The governing rule: **a review that reads evidence is not a review.** A review that
independently reproduces behavior is. If the reviewer's basis is "the suite is green and
the transcript says 52/52," it has graded a transcript, not verified a system.

## The adjacent-doorway failure mode

This is why a fully green suite can coexist with real defects, and it is the single most
important pattern in this file.

**Tests test what the implementer thought to test.** When the implementer misunderstands
a contract, they write a test that guards the doorway _next to_ the one that is actually
open. The test passes honestly. The hole remains.

Worked examples, all three confirmed by direct reproduction in one session where a 52/52
green suite had been accepted:

| Contract                | What the test checked                | Where the hole actually was                                                             |
| ----------------------- | ------------------------------------ | --------------------------------------------------------------------------------------- |
| No secrets in artifacts | `api_key` key in packet **metadata** | `apiKey` inside **embedded base64 source bytes** — validator never decoded the manifest |
| Governed payload schema | **Missing** required fields          | **Wrong-typed** present fields (`current_question=12345`, `owner={'x':1}`)              |
| Retry idempotency       | **Sequential** identical retries     | **Concurrent** identical retries — one won, one raised a uniqueness error               |

Each test was correct about its own assertion. Each sat one inch from the defect. A
reviewer reading "privacy test: PASS" would sign off on a leaked credential.

**Practical consequence:** when checking whether a contract is enforced, do not ask "is
there a test for this?" Ask "what is the _nearest variant_ of this input that the test
does not cover?" Then send that variant through the real code path. Nearest variants
worth trying by default:

- Encoded / nested / embedded position instead of top-level
- Wrong type instead of absent
- Concurrent instead of sequential
- Expired-then-reacquired instead of never-valid
- Semantically nonsensical instead of structurally malformed

## Handling contradictory verdicts

When reviewer A says PASS and reviewer B says REQUEST_CHANGES on identical code, you
have exactly one correct move: **reproduce the specific claims yourself.**

Do not resolve it by:

- Recency ("the newer review is better informed")
- Authority ("the deeper model is more reliable")
- Vote counting ("two of three said PASS")
- Effort ("that one took longer, it must be more thorough")
- Convenience ("PASS unblocks the work")

Do resolve it by writing a probe per disputed finding, running it against an isolated
copy, and reporting each claim as **reproduced**, **not reproduced**, or **unproven**.

Report all three states honestly and distinctly. In the worked session, one of four
claimed defects (a lease-fencing Critical) did **not** reproduce — but source inspection
showed validation ran outside the write transaction, so the correct status was
_unproven, not disproven_: a real structural weakness whose specific exploit claim was
unsupported. Collapsing that into either "confirmed" or "false positive" would have been
dishonest. Say which one you mean.

A reviewer being wrong about one finding does not discredit its other findings. Grade
claim by claim, never reviewer by reviewer.

## Probe hygiene

- Copy the target to `/tmp` and probe the **copy**. Never mutate canonical state.
- Probe the **real code path** (import the actual module), not a reimplementation.
- Decode what you are asserting about. If content is base64/compressed/nested, decode it
  and search the decoded bytes. Asserting on the wrapper is how the leak above survived.
- Prefer a failing probe that prints the leaked value over one that prints `False`. You
  want the evidence quotable in the report.
- State the probe command and its literal output. "I verified X" is not evidence; "I ran
  this and got that" is.

## When your own earlier verdict was wrong

Retract it plainly and early, in the same message where you present the contradiction.
Do not soften it, do not bury it under the new findings, do not reframe it as "new
information emerged" when the truth is the earlier review was insufficiently rigorous.

State plainly: what you reported, that it was wrong, what the actual status is, and what
real-world consequence followed. If the blast radius was zero because a boundary held,
say so — that is a genuine finding about the system's safety design and it belongs in
the report, but it is context for the error, never an excuse that dilutes it.

## Do not re-dispatch a review that already landed

Before dispatching a review subagent, check the evidence directory for a completed
verdict artifact covering the same scope. A review loop that keeps re-reviewing an
already-decided question burns wall-clock and tokens and produces no new information —
and when reviews are long-running, a redundant one can time out and look like a failure,
masking that the real answer was already on disk.

Symptom to watch for: the user says you "kept getting stuck." Check for completed
artifacts before re-dispatching anything.

## Acceptance gate

Never record an acceptance whose Definition of Done includes "zero open Critical and
Important issues" on the strength of a reviewer's assertion alone. Either you reproduced
the closure of each finding, or the DoD item is unverified and acceptance must not be
recorded. Reviewer verdicts are input to that judgment, never a substitute for it.
