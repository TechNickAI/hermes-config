# Breaking the review spiral

Read this when a finding lands in code you added to answer an earlier finding, or when
one area keeps drawing findings round after round. The shape: a reviewer raises an edge
case, you add a mechanism for it, the next round finds an edge case in the mechanism.
Each fix is locally reasonable and the PR gets steadily worse.

## First move: stop adding mechanism

Fix the original defect the simplest way that works, then look at what you added since.
If round N's findings share round N-1's theme, the round N-1 fix was the mistake: revert
or simplify it rather than guarding it. Prefer deleting a fragile mechanism you added
over protecting it, but never delete a control the finding itself is about.

## A real fix that draws a race in its own code

Usually the fix made a second writer of shared state. Don't add locks or reloads on top.
Redesign so one task owns the state: move the new work into the task that already owns
it, ahead of its gate. Before giving a second task recovery or cleanup work on shared
state, check whether the owning task already runs on a schedule regardless of the gate
that blocked it; if so, move the work there instead of adding a writer. Better still,
pick the single-writer shape on the first fix.

## An optional sub-feature that keeps drawing serious findings

When an extra the user did not explicitly ask for (a growth mode, a scale-up path, a
convenience) draws high-severity findings round after round, ask the user whether to
remove it from this PR, or ship it unreachable (not just off by default: a setting users
can flip still exposes the defect). If they agree: take it out or make it unreachable,
add a test proving it cannot run, reply to its findings saying so, and bring it back in
its own PR with tests for what reviewers raised. Never switch off the behaviour the user
asked for without asking.

## Hardening a fallback you added this round

If the existing failure is already loud and retried (a scheduled job that exits non-zero
and reruns), decline in one line naming that safety net. Don't add a second fallback
layer, especially one that multiplies requests under rate limiting.

## "A restart inside window W loses the new behaviour"

When losing it just falls back to the pre-PR behaviour, it is not a regression. Say
exactly that, and don't persist new in-memory state through a checkpoint layer to cover
a few-minute window.

## "This threshold is invented"

When the user gave only a qualitative rule ("clearly", "thin", "fast"), label the number
as the implementer's choice in the code comment and the PR body, keep it as a named
knob, and decline in one line. Tell the user the number in the report and that it is one
setting to change. A policy number the user must own (sizes, prices, limits, windows) is
different: get their number before opening the PR.

## When a reviewer's "safe" fix changes outcomes

On decision, scoring or pricing logic, a fix that reads as safety ("hold on unknown",
"never cross the spread") can change results. After a round that touches decision logic,
re-run the before/after replay or a fixed sample on the new head. If it regresses,
ablate one fix at a time, revert the one that costs, and reply with the measured
difference.

## When the test you wrote stays green under sabotage

Run it against the base branch's version of the file. Green there means the test does
not demonstrate the defect: either the premise is false or the test misses it. Keep
investigating until you know which. Decline only after you have found the existing
mechanism that handles the case (instrument it) and aimed the sabotage at that mechanism
until the test goes red; then keep the test as a pin and cite it. If you can't find the
mechanism, the test is wrong, not the finding.
