# Lenses

A steward pass picks a project, then picks a LENS to apply to it. Lenses are not
separate scheduled jobs on separate clocks. That design was replaced because it cannot
work at portfolio scale: with thirteen projects, a daily one-project zoom-out is a
thirteen-day rotation and a weekly one-project creativity lane is a thirteen-WEEK
rotation. Measured after 31 passes, eleven of thirteen projects had never had a zoom-out
and creativity had never fired at all.

Rotation is solved exactly once, by the dispatcher. Every lens inherits it.

## Choosing the lens

Pick the project first using the dispatcher's rules (claim lock, starvation floor,
irreversible deadlines, value-to-cost). Then ask, in order:

**1. Does CREATIVITY trigger?** If yes, use it. It outranks the others because a project
whose frame is exhausted will burn an advance pass executing a roadmap that inherits the
dead assumption.

**2. Is this project DUE for a zoom-out?** Any one of these is enough:

- five or more of its own passes since its last zoom-out
- seven days since its last zoom-out
- it has never had one and has had at least one advance pass
- two consecutive substantive passes produced no decision-relevant answer

**3. Otherwise, ADVANCE.** The default, and the common case.

State the chosen lens in one line at the top of the pass, and tag the log line with it.

A pass may switch lenses mid-flight. If an advance pass discovers drift, do the zoom-out
then and there. If a zoom-out returns a bad verdict and the existing roadmap would
continue the displacement, apply creativity in the same pass. Deferring a lens to a
future pass is the failure this design removed.

## Lens: ADVANCE

Tactical. Verify what a domain agent claims, audit their artifacts, dispatch the top
open roadmap item, issue and read back work orders.

Verification consumes the agent's OWN outputs: logs, row counts, files, error messages,
exit codes. It never regenerates their analysis.

## Lens: ZOOM-OUT

Ask whether the project still points at the thing the principal actually wants. The
advance lens cannot see this by design: it verifies that work was done correctly, not
that it was the right work.

This lens earned its place immediately. Its first run found six weeks of drift on a
project where every commit was infrastructure, the actual deliverable was untouched, and
both review gates were silently dead. Its second run found that a charter's own primary
test had never been dispatched across eleven work orders.

Do not ask only whether the work is "related" to the charter. Related work can still be
displacement.

1. **Split the charter into its independent promises.** A charter with two valuable
   promises can be half-abandoned while looking healthy, because activity on one masks
   zero activity on the other. Check each promise for its own dispatch history.
2. **Count work orders per promise. Zero is the finding.** A promise named as the next
   action with no work order anywhere has been sitting unassigned while every state file
   looks fine.
3. **Apply the more-code / more-data test** to whatever is consuming the passes: if this
   collection or build succeeds perfectly, which project decision changes, under which
   result? State the ranges and the action each would cause. Ask whether the current
   unknown is the thing the charter set out to learn, or merely a tool for learning it.
   If no result changes a decision, stop or subordinate the work. Check the UNIT a power
   calculation counts. Adding more rows inside the same clusters adds no independent
   observations.
4. **Name the falsifiable question answered since the last zoom-out.** Activity without
   an answered question is not movement. Two substantive passes with no
   decision-relevant answer is a stall even if code and data grew.
5. **Check the kill criteria are still honest**, that shipped work reaches its intended
   user, and that review gates actually run. Reproduce failures before attributing them.
   A kill clause reading "exhausted across all tiers" cannot be claimed while a tier was
   never tested.
6. **The stranger test.** Hand the artifacts to someone who never read the charter. What
   would they say this project is? The gap between that and the charter's own sentence
   is the drift.

Beware the deadline trap: an irreversible clock on one promise is the easiest way to
spend all attention on the promise with a deadline instead of the promise with the
value.

Verdict is ON-COURSE, LOPSIDED, or OFF-COURSE. Write it into the project's state and log
it.

## Lens: CREATIVITY

For one situation only: a project has genuinely run out of moves. This is NOT a
brainstorming slot. Generating ideas on top of an untested pile is waste, and the
failure mode being guarded against is a portfolio that accumulates unexecuted ideas
instead of executed tests.

**Triggers, judged per project.** An open roadmap item elsewhere never suppresses
creativity here. Roadmap exhaustion is not required. Trigger when any of these holds:

1. Two or more materially different approaches failed for the same underlying reason.
   The shared assumption is the finding to investigate; do not propose a third approach
   that inherits it.
2. Two consecutive substantive passes produced no decision-relevant answer. More code,
   rows, infrastructure, or restated next actions do not count as answers.
3. The remaining affordable roadmap items inherit a load-bearing assumption already
   indicted by evidence. "Untested" does not mean "meaningfully different."
4. The latest zoom-out verdict is LOPSIDED or OFF-COURSE and executing the existing
   roadmap would continue the displacement.

**Method:**

1. Read the project's full state, especially the dead-ends and decisions records. Do not
   re-propose a buried idea without saying why the burial no longer holds.
2. Say narrowly what died, what the failure proves, and what it does NOT prove.
3. **Go outward.** Find who is succeeding at this: papers, repositories, forums,
   practitioner writing. How does their method differ? People with a real edge rarely
   publish it, so read the shape of their infrastructure and their complaints rather
   than their success claims. Delegate this research rather than doing it inline.
4. Negate each load-bearing assumption one at a time: the domain, the time horizon, the
   signal's ROLE (trigger versus filter versus veto), the latency regime, entry versus
   exit, the counterparty, the unit of analysis.
5. Write ranked falsifiable items into the roadmap, each with a kill rule, an executor,
   and a cost. Items only. Do not dispatch in the same pass unless one is obviously
   cheapest-first and the domain agent is idle.

## What lenses cannot see

Cross-project pattern: whether the PORTFOLIO is aimed correctly, whether five projects
are different masks on the same dead assumption, whether the mix matches what the
principal said matters. No per-project lens reaches that, so it stays a separate
periodic review.
