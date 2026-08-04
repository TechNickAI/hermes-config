# Choosing the best move

Worked examples for the four-step decision in SKILL.md. The value of these is not the
conclusions, which are specific to one portfolio on one day. It is the SHAPE: what a
finished piece of reasoning looks like, and what it looks like when it stops early.

## Why the examples matter more than the rule

The rule "reason to the best move" is easy to agree with and easy to not do. The failure
is not disagreement, it is stopping at step 2, naming the obstacle, and treating the name
as the answer. "This project needs verification" is a category, not a move. "Re-derive the
cluster key from the agent's own committed artifact and check it against the hardcoded
constant" is a move.

A finished step 3 has candidates you could hand to someone else and they would know what
to do tomorrow morning.

## The tell that a pass stopped early

- The chosen move is described in the same words as the obstacle.
- All three candidates are the same move at different depths.
- The runner-up is a strawman, obviously worse, included to satisfy the requirement.
- No candidate would cost money, ask anyone anything, or change what the project is.

That last one is the strongest signal. If every candidate is something the steward can do
alone with files it already has, the option set was never opened.

## Worked example 1 — the resource nobody absorbed

**Project:** on-chain wallet copy trading. Six research cycles had returned "not
profitable" while profitable wallets kept being found, and no cycle explained how.

**What the old procedure did:** found that two disjoint 31-wallet populations were both
being called "the 31 wallets" (intersection: zero) and dispatched an order to reconcile
the population-identity defect. A real defect, correctly attributed to the steward rather
than the agent.

**Step 1, the gap.** We want to know whether copying good wallets makes money. We have
spent six cycles proving specific copy mechanisms fail, and still cannot say how the
profitable wallets we keep finding got profitable.

**Step 2, what is in the way.** Not a belief needing verification. **A thing that exists
in the world, which the principal had already handed over.** Fifteen hours earlier he had
supplied a vendor API key with an explicit instruction, and named two more vendors and the
project's origin app. Every prior cycle ran on self-sourced data carrying a survivorship
problem the project had never solved. The vendors exist to solve exactly that.

**Step 3, candidates.**

- (a) Dispatch the population-identity reconciliation. *If perfect:* the audit trail under
  six negatives becomes trustworthy. It produces no new candidate, no mechanism, no dollar.
  All six results stay negative either way. **Delta: near zero.**
- (b) Spend the metered request budget on a vendor-sourced leader set and check whether
  wallets selected by an INDEPENDENT party survive our screens. *If perfect:* the
  survivorship problem gets an outside check for the first time. Failure is the strongest
  evidence yet toward a real wall; success is a candidate set that was never ours to bias.
  **Delta: large in both directions.**
- (c) Ask whether the origin app has an internal API. The principal raised it himself and
  it went unanswered. **Delta: moderate, costs one line.**
- (d) Answer his actual instruction: with three new sources on the table, write the
  one-page plan for what each is good for BEFORE spending a request.

**Step 4, choice: (d) then (b).** He asked for a plan before harvesting in those words,
and the budget cap is small enough that spending it unplanned is the expensive mistake.

**Runner-up: (a).** It is real, it is the steward's own defect, and it is exactly what the
old procedure would have chosen for the fifth time. It loses because reconciling the audit
trail beneath six negatives cannot produce a positive, and a vendor-sourced leader set can.

**The general lesson:** ten consecutive passes ran after that key arrived and none picked
this project. The vendor names appeared nowhere in its state files. **No rule anywhere read
"the principal gave us something new,"** which is why the means ledger and its
surprise-correction hook exist.

## Worked example 2 — when the old procedure was already right

**Project:** an LLM stock-analysis desk. 71 evidence packs from a three-day sprint.

**What the old procedure did:** discovered fundamentals were real in 0 of 71 packs, social
in 3 of 71, both in zero, meaning every adverse finding had been measured on packs with
the evidence removed. Dispatched: build one complete pack and report what completeness
costs.

**New procedure, step 3 candidates.**

- (a) Re-run the corrected subset. *Delta:* a cleaner version of a measurement already
  known to have run on degraded input, and downstream of the cost number anyway. **Near zero.**
- (b) Build one complete pack, measure what completeness costs. *Delta:* decides whether
  the roadmap is reachable at all. **Large.**
- (c) Build it on a ticker the principal named as a real use case, rather than an arbitrary
  one. *Delta:* identical cost, plus the first artifact in the project's history touching a
  stated use case.

**Choice: (c).** Same move as the old procedure, improved by one ticker.

**This example is in the file deliberately.** A redesign that reverses every prior decision
is not a better procedure, it is a contrarian one. The new procedure agreeing with the old
call is evidence it is tracking value rather than novelty.

## Worked example 3 — the deleted constraint

**Project:** a cross-chain yield scout for two capital sources.

**What the old procedure did:** caught that a domain agent had declared a benchmark "not
measurable from here" while a different agent was using that exact ledger as a ground-truth
gate in another thread. Reproduced the number independently, then dispatched an order to
restate the verdict against it.

**Step 2, what is in the way.** Two things, and the second is bigger. The verdict does need
restating. But underneath it, **the project had been reasoning inside constraints the
principal deleted that morning:** a non-US capital source with different tax treatment, and
a concentration ceiling reached at the incumbent venue, meaning the incumbent was no longer
a valid comparison base.

**Step 3, candidates.**

- (a) Dispatch the verdict restatement. **Already in flight; does not need this slot.**
- (b) Re-run the entire opportunity scan with the citizenship filter REMOVED and the
  incumbent excluded as a comparison base. *Delta:* potentially large. Every prior scan
  silently applied a filter now voided, so **the whole rejected set is suspect, not just the
  one candidate.**
- (c) Ask about the new capital source's size, liquidity, and risk tolerance. Needed
  eventually, not blocking.

**Choice: (b).** One deleted constraint invalidates a filter that ran on every candidate
ever scanned. That surface is larger than any single verdict.

**The general lesson:** when the principal deletes a constraint, the question is never
"which pending decision changes." It is **"what did we already reject under that
constraint."** A constraint deletion is retroactive.

## The pattern across all three

In both cases where the new procedure changed the decision materially, step 2's honest
answer was **"a resource or constraint the principal supplied that no file has absorbed."**
Neither was reachable by better auditing, and both existed only in chat scrollback.

A steward that only reads project files can never find them. This is the argument for
treating the principal's own messages as a first-class input to every pass, and for a
means ledger with a forcing function on the READ.
