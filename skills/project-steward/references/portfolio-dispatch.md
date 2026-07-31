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

**1. Claim locks first.** Create a lock directory per project before dispatching
anything. Skip any project already claimed. Treat a claim as stale only after a timeout
AND proof the owning process is dead.

This exists because two overlapping passes once issued the same work order twice. A
mkdir-based lock is atomic on every POSIX filesystem, which a check-then-write is not.

Release claims at the end of the pass, including on the failure path.

**2. Starvation floor, mechanically.** Enumerate every project directory on disk. For
each, search the WHOLE log for its last pass, including lines that mention several
projects. A project never picked counts as oldest. Sort eligible projects oldest-first
and fill available slots in that order.

This must be mechanical enumeration, not the model recalling which projects it has been
neglecting. Before roadmaps and this floor existed, two projects absorbed eight of
thirteen passes while five starved entirely.

**3. Irreversible deadlines.** A project with a real clock outranks one without. Beware
the inverse failure: a deadline on one promise is the easiest way to spend all attention
on the promise with a clock instead of the promise with the value.

**4. Value to cost.** Among the rest, prefer a pass that moves something from unknown to
known. Prefer a project that is moving over one that is stalled, unless the stalled one
has a clear next action.

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
- Release claim locks.
