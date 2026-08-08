# Portfolio dispatch

How one scheduled job decides where attention goes, without starving projects or
double-dispatching work.

## Slots

Read the capacity signal first, then set the slot count:

| signal   | slots | behaviour                                                          |
| -------- | ----- | ------------------------------------------------------------------ |
| throttle | 0     | do nothing, stay silent                                            |
| reduce   | 1     | the single most important project, no panels, no parallel children |
| maintain | 2     | normal judgment                                                    |
| increase | 3     | do MORE, see below                                                 |

**Slots are a CEILING, never a quota.** If only one project has a next action worth
taking, take one and leave the others empty. A three-slot pass that does one real thing
and two manufactured things is worse than a one-slot pass.

**`increase` means unused capacity is being wasted, not saved.** Keep working the
project you picked until it genuinely stops moving, then pick up a second rather than
ending early. Spend the surplus on depth: verify a claim properly, run a review panel,
do the zoom-out you have been deferring. Abundance still never justifies noise. If
nothing deserves a pass, stay silent anyway.

## Filling slots, in strict order

**1. Claim the project atomically.** Two overlapping passes once issued the same work
order twice, so a pass must claim a project before dispatching anything, and the claim
must be atomic rather than check-then-write.

Two mechanisms work. A `mkdir`-based lock directory is atomic on every POSIX filesystem
and needs no dependencies, but nobody garbage-collects it: a pass that dies mid-flight
leaves a lock that blocks the project until a human clears it, so treat a claim as stale
only after a timeout AND proof the owning process is dead, and release claims at the end
of the pass including on the failure path.

A task-tracking database is better if you already have one, because the claim and the
state record are then the same object. **The claim is the idempotency key, not the act
of looking first.** Listing open tasks, seeing none, and then creating one is a race:
both passes see nothing and both create a task. Keying the create on a stable identifier
is what makes it atomic, and a second create with the same key returns the existing id
instead of a duplicate.

The key must be scoped to the attempt, not to the project forever. A constant
`portfolio:<project-slug>` key is a permanent claim: when a project legitimately needs a
second pass after its earlier task is complete, the create returns the historical task,
the instruction below treats it as another pass's ownership, and the project becomes
permanently unpickable. Scope the key to the pass instead so it is stable within a pass
(atomic against a concurrent pass) but distinct across passes. Use a timestamp at second
or finer resolution — date-only collides when passes run more than once per calendar
day:

```bash
<task-cli> create "<project>: <deliverable>" --idempotency-key "portfolio:<project-slug>:<pass-start-timestamp>"
```

If the id that comes back is not the one you just made, another pass owns that project:
skip it and take the next eligible one.

**2. Starvation floor, mechanically.** Enumerate every project directory on disk. For
each, search the WHOLE log for its last pass, including lines that mention several
projects. A project never picked counts as oldest. Sort eligible projects oldest-first
and fill available slots in that order.

This must be mechanical enumeration, not the model recalling which projects it has been
neglecting. Before roadmaps and this floor existed, two projects absorbed eight of
thirteen passes while five starved entirely.

**Watch for the loop where the work generates its own next reason.** The starvation
floor gets out-argued by a project that keeps manufacturing fresh urgency. One steward
ran six consecutive passes on a single project; every override was individually
defensible ("an unaudited kill on the only live-money project outranks rotation"), but
auditing the agent's answer produced the next work order, which produced the next audit.
The justification regenerated every pass and never handed the slot back, while every
other agent in the fleet went more than a day with no contact.

The tell is not the count of consecutive picks. It is that **the reason to pick this
project again was created by the last pass on this project.** When that is true the
urgency is self-generated, and the honest read is that the project is fine and the fleet
is starving. An agent that answers in forty minutes will always look more urgent than
one that has been silent for a day; that is a property of latency, not importance.
Before any discretionary pick, ask which agent has been quiet longest and whether you
created this urgency yourself last pass.

Prose alone did not fix this. The pass that ran after this guidance was written read it,
named its own displacement accurately, and picked the same project anyway. Treat a
structural constraint as the real fix and the guidance as a secondary aid.

**3. Irreversible deadlines.** A project with a real clock outranks one without. Beware
the inverse failure: a deadline on one promise is the easiest way to spend all attention
on the promise with a clock instead of the promise with the value.

**4. Value to cost.** Among the rest, prefer a pass that moves something from unknown to
known. Prefer a project that is moving over one that is stalled, unless the stalled one
has a clear next action.

## A state tracker is not a dispatcher

If you adopt a task board to track project state, keep it to tracking. Many boards ship
a dispatcher that spawns a worker automatically when a task is assigned, and turning
that on quietly changes the whole system.

What happened when one steward enabled it: creating a card became the cheapest possible
way to dispatch, so work concentrated on the one agent that ran locally and could be
dispatched that way, while agents on other machines went silent for over a day. A card
filed merely to _record_ an order already sent by hand spawned a second worker that
redid the work. A card that reached the board's triage state was auto-decomposed by an
LLM into four child tasks. None of these are tracking failures; they are all the
dispatcher.

The distinction that matters:

| Concern                                                 | Belongs to              |
| ------------------------------------------------------- | ----------------------- |
| What is in flight, blocked, or waiting on the principal | The board               |
| Priority ordering and history between passes            | The board               |
| Which agent gets told to do what, and when              | The steward, explicitly |

Most boards expose a config flag to disable auto-dispatch while leaving the CLI and
database fully functional. Prefer that. **A card should be a record, not a trigger.**

Two properties follow, and both are worth having. Local and remote agents are treated
identically, since nothing is auto-dispatchable. And the self-feeding loop described
above gets more expensive to run, because a card can no longer manufacture an agent's
next hour of work without the steward deciding to.

But disabling auto-dispatch does not break the loop, and claiming it does leaves the
project starving. The steward can still select the same project every pass and
explicitly dispatch it; the pass after that audits the answer, manufactures fresh
urgency, and selects it again. Prose guidance against this already failed: the pass that
ran after the guidance was written read it, named its own displacement accurately, and
picked the same project anyway. The agent that manufactures the urgency cannot be the
only judge of whether the urgency is real.

The structural remedy is a rotation gate computed before the pass, outside the agent's
judgment. A pre-pass step enumerates every project, reads the last-pick time for each
from the log, and marks a project ineligible when it has taken N consecutive passes (N=3
is the value in use). The agent receives this eligibility list as context, not as a
suggestion it can override. The property that makes it work is that it is computed and
injected, not argued for: the pass does not decide whether it is excused, because the
pass is the thing being constrained. Treat this gate as the real fix for the
self-feeding loop and the guidance as a secondary aid.

## Dispatch is visible or it did not happen

A board row is invisible to the principal. If work orders move from a channel he reads
to a database he does not, the fleet appears to stop working even as throughput stays
flat. This is a real reported failure: the principal asked why the pace had dropped
during the system's most productive day on record, because every dispatch that day was a
silent row.

So the channel is the dispatch surface and the board is the state surface. **Record the
order on the board first, then send it to the channel.** The board write is the atomic
claim that prevents a second pass from dispatching the same work; the channel message is
the visible side effect. Sending first and recording second reintroduces the
duplicate-dispatch bug this whole section exists to prevent: two overlapping passes both
send before either records its card, and a crash after sending leaves no state so a
retry sends the order again. The claim must come before the side effect, every time. If
the board offers event subscriptions that push to a channel, wire them up, but **verify
delivery end to end before relying on them**, subscribe a throwaway task, complete it,
and confirm the message actually arrives. One steward wired subscriptions, documented
them as the fix, and only discovered afterward that the notifier never advanced its
cursor and no message was ever sent.

## Review panels gate findings, not passes

Run a multi-model review when a FINDING needs adversarial pressure, not on every pass.
An earlier "panel every pass" doctrine cost roughly twenty-three dollars over four days
and produced mostly agreement with work that was already sound.

## Parallel children

When dispatching several projects in one pass, send them as one batch of subagents
rather than sequentially.

Size the work before dispatching. An N-item loop can exhaust a subagent's time budget at
dispatch time even when the child behaves perfectly: two children given eleven items
each covered only six before timing out. Multiply items by realistic per-item seconds,
target well under the ceiling, and shard accordingly. Rank items by value so that a
truncated run still covers the part that mattered.

Require every child to end with an explicit coverage line naming what it did and did not
reach. A timeout returns no summary at all, so without that line the parent cannot
distinguish a complete audit from a truncated one.

Children report findings back. They do not post to chat and they do not ask the
principal questions. The parent decides what reaches a human and does all the sending.

**Verify before relaying.** Child summaries are self-reports. If a child claims a file
was written, a work order was sent, or a number was verified, check it before repeating
it. This rule caught a "backfill running" report from a process that had been dead for
seven hours.

Then synthesize. One pass, one voice: what moved, what is now known that was not, and
the one decision the principal faces if there genuinely is one. Do not concatenate child
reports.

## After the pass

- Append ONE line to the portfolio log, tagged with the lens used.
- Rewrite the project's "now" file rather than appending to it.
- Append to the project's log and update its roadmap.
- Record decisions with their re-open conditions.
- Release claim locks. For a `mkdir` lock, remove the directory. For a database-backed
  claim, complete or close the task so the claim is released by the same lifecycle that
  owns it. A claim left open by a crashed pass needs the same staleness treatment as a
  stale lock directory: after a timeout and proof the owning process is dead, another
  pass may treat the claim as abandoned and close it. Do not reuse a completed task's id
  on a later pass; a new pass takes a new attempt-scoped key.
