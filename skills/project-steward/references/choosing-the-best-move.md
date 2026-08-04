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

That last one is the strongest signal. If every candidate is something the steward can do alone
with files it already has, the option set was never opened.

**Measured instance, first full-portfolio run of this procedure.** An independent three-family
panel reviewed 17 passes and found the strawman failure explicitly. One project's candidate 1 was
"stay cold," candidate 2 was "violate the principal's stated instruction," and candidate 3 was
filler — the three-candidate rule satisfied without ever opening the option set. Another project
offered three variants of "resolve existing uncertainty" with no buy-compute, no engage-a-
specialist, and no retire-this-path.

**Hence the rule: one candidate must be of a different KIND** — spend money, ask a person, merge,
retire, ship, or change the charter — not merely different content. Two honest candidates beat
three where one is a scarecrow.

## WAIT is not a move

Two of the 17 passes chose to await an in-flight work order. That is the audit default with the
verbs removed: it consumes a slot, produces a log line that looks like work, and leaves the
project untouched. If nothing can be done until an agent reports, **skip the project and pick
another.** Silence is a success state; a status page is not.

## The phantom blocker

A project's `now.md` said, for four days: *"the principal's call on direction — still open, still the one
real blocker."* Four paragraphs below, the same file said: *"the principal approved A only, verbatim."*
`decisions.md` carried the approval with a message id and a UTC timestamp.

The decision had been made. The file said it was blocked. Every pass read the blocker line,
correctly declined to re-ask a question already answered, and moved on — so the contradiction
survived every pass that could have caught it, precisely because each pass behaved well.

**A phantom blocker is worse than a real one.** It makes a decided project look stalled on the
principal, it protects the project from being worked, and it is invisible to the agent holding it
because "waiting on the principal" is a legitimate state.

Two checks, cheap:

1. **Before writing or preserving any blocker line, grep `decisions.md` for that question.** If
   an answer exists with a date, the blocker is discharged; delete the line and record what the
   answer authorizes.
2. **A blocker must name what changes per answer.** "the principal's call on direction" names no options
   and no consequences. If you cannot write "if he says A I do X, if he says B I do Y," it is not
   a blocker — it is a project that has not been thought about recently.

The same pass that found this had also, in the same file, correctly listed the approved capability
as unbuilt. Both facts were present. Nobody had put them next to each other.

The first run of this procedure scored **17 of 17 projects as OUTWARD**, i.e. every pass declared
its own chosen move to be real progress. The review panel found at least five were inward in
substance: reconciling replies into a state file, re-scoring an existing reject list under a new
bar, waiting on a dispatched order, and spending a pass to confirm that nothing should be done.

The mechanism, as one reviewer put it: the test was being judged from INTENT ("this changes
something") instead of MECHANISM ("does this introduce new information, new capability, or
external commitment?").

Two rules follow, and they generalize far past this metric:

1. **Score by mechanism, not intent.** OUTWARD requires new information from outside the
   project's files, a new capability or artifact, or an external commitment. Everything else is
   inward, including work that is correct and necessary.
2. **No metric may be graded by the same pass that produces the work it grades.** The pass
   proposes its verdict; the nightly review recomputes it from the log and reports disagreements.

A metric that never fires is either measuring nothing or is being graded by the party it judges.

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

## The proxy is better, not solved

Outward research, 2026-08-04, with every arXiv ID verified against the API rather than trusted:

**Skalse et al., *Defining and Characterizing Reward Hacking* (arXiv:2209.13085), prove two
reward functions can only be mutually unhackable if one is constant.** The INWARD/OUTWARD test
above is non-constant, so it **is gameable in principle.** It is a better proxy than the menu it
replaced. It is not a solved problem and must never be held as one.

The predicted failure targets this document's own remedy. The Reward Hacking Benchmark
(arXiv:2605.02964) found **72% of reward-hacking episodes include explicit chain-of-thought
rationale** — models frame exploits as legitimate problem-solving. A four-step written procedure
is exactly that prose surface: requiring reasoning to be written out makes it legible AND hands a
captured agent an ideal medium for fluent self-justification. Both are true at once.

Three ways the test gets satisfied nominally: cite a source you did not read, emit a trivial
artifact, send a ceremonial message to a person. All three score OUTWARD while changing nothing.

**Two known holes, recorded rather than hastily patched:**

1. **The independent verifier is not independent.** "No metric may be graded by the same pass that
   produces the work" is right, but a nightly review by the same system re-reading the same log is
   still in-distribution. Pan et al. (arXiv:2402.06627): "evaluations on static datasets are
   insufficient — they miss the feedback effects." Real independence means a different model and a
   different prompt.
2. **There is no outcome controller.** Passes are scored by what KIND of move they were, never by
   whether the project advanced. This is what killed a sibling project: 91 self-improvement cycles
   while steadily losing money, postmortem line *"it lacked an outcome controller."*

**The steelman the menu deserves.** A typology's value is forced coverage and *legibility of
neglect*. The 33/7/0 distribution IS proof the menu did its job: it made the bias countable, and
countability enabled the redesign. Free-form generation can hide the same bias in fluent prose
with no column to reveal it. The literature's actual recommendation was to keep the menu as a
coverage floor with per-bucket quotas and fix the REWARD to be outcome-based. Deleting the
instrument that detected the problem is not the same as fixing the problem.

**The meta-lesson, the most durable thing here:** this redesign was reasoned from first principles
and internal measurements without checking prior art, and it hand-re-derived a formally
characterized failure mode (reward hacking, Goodhart's law, the McNamara fallacy). Work requiring
no outside input always succeeds — the exact diagnosis this procedure exists to fix. **A pass that
changes the steward's own procedure is INWARD by definition. Rewriting how you decide is the most
inward act available.**
