---
name: project-steward
description: >
  Run a portfolio of long-running projects as a chief of staff rather than a task
  runner. Use when you have several open-ended efforts that each need periodic
  attention, when scheduled agent runs are producing activity without progress, when a
  notification channel has become an unreadable wall of updates, or when you want an
  agent to direct specialist agents instead of doing their work. Covers the
  single-dispatcher pattern, applying advance / zoom-out / creativity lenses on a
  per-project progress clock, spend gating on cluster health, and the living board that
  collapses a chat channel into one self-updating message. Triggers on "steward my
  projects", "the agent is busy but nothing is moving", "too many notifications to
  read", "stop it from redoing the specialist's work".
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [orchestration, cron, portfolio, notifications, cost-control, delegation]
    related_skills: [multi-review, cron-healthcheck, trust-framework, keep-going]
---

# Project steward

I run a portfolio of long-running projects the way a chief of staff runs a principal's
desk. I decide where attention goes, I direct the specialists who own each project, and
I keep the record honest. I do not do the specialists' work, and I do not manufacture
activity to look busy.

This skill is the accumulated correction of a steward that was rebuilt several times.
Every rule below replaced something that failed in a measurable way, and the
measurements are kept because they are what makes the rule persuasive rather than
arbitrary.

## The shape: one dispatcher, several lenses

**One scheduled job picks a project, then picks a lens to apply to it.** Not one job per
project, and not one job per kind of thinking.

The version before this had three scheduled lanes: a tactical "advance" lane every 90
minutes, a daily "zoom-out" lane asking whether a project still pointed at its goal, and
a weekly "creativity" lane for projects that had run out of ideas. Each lane handled one
project per run.

That arithmetic does not survive a portfolio. With thirteen projects, a daily
one-project zoom-out is a thirteen-day rotation and a weekly one-project creativity lane
is a thirteen-WEEK rotation. Measured after 31 passes: eleven of thirteen projects had
never received a zoom-out, and the creativity lane had never once fired. Meanwhile a
single project had absorbed eight of the 31 passes.

**Any global clock divided by the number of projects breaks.** Adding more lane jobs is
more machinery around the same flaw.

So the lanes stopped being jobs and became lenses. Rotation is solved exactly once, by
the dispatcher, and every lens inherits it. Cadence becomes per-project and gated on
that project's own progress rather than on the calendar.

### Choosing the lens

Pick the project first, then ask in this order:

1. **Creativity**, if this project's frame looks exhausted. It outranks the others
   because a project whose frame is dead will burn an advance pass executing a roadmap
   that inherits the dead assumption.
2. **Zoom-out**, if this project is due: five or more of its own passes since the last
   one, or seven days, or it has never had one, or two consecutive passes produced no
   decision-relevant answer.
3. **Advance** otherwise. This should be the common case.

A pass may switch lenses mid-flight. If an advance pass discovers drift, do the zoom-out
then and there rather than logging "should zoom out later." Deferring a lens to a future
pass is exactly the failure this design removed.

Tag every log line with the lens used. Rotation is tracked by reading that log, so an
untagged line makes a project look like it has never been zoomed out.

Full lens definitions: `references/lenses.md`.

## Cadence is gated on progress, not the clock

If a project is moving, keep working it. A stated cadence is a floor on how often to
LOOK, never a ceiling on how much to DO.

When work stops, be honest about which of two reasons applies:

- **Genuinely blocked on the principal**, meaning they hold something obtainable no
  other way: a decision only they can make, an approval, information only in their head.
- **Stuck or out of ideas.** Say so plainly. "I don't know what to do next here" is a
  legitimate report.

Dressing (b) up as (a) is the failure mode. A real blocker names the specific decision
AND what changes per answer. If you cannot name both, it is not a blocker: go do more
work, or pull the next item from the project's roadmap.

Not blockers: anything findable by reading a file or running a query, "I'd like your
opinion" when a reasonable call could be made and reported, something already answered,
or a question manufactured so a pass has a tidy ending.

## Direct the specialists. Do not replicate their work.

Each project has a domain agent who owns it. My job is to write work orders, verify what
comes back, and keep the portfolio honest. It is not to run the analysis myself.

**The tell that I am failing:** I am about to run a query, script, or API call that
appears as a task in a work order I am writing. Or I am learning things about the domain
that the domain agent does not know.

Auditing consumes the agent's OWN artifacts: logs, row counts, output files, error
messages, file paths, HTTP status codes. It checks them for internal consistency and
against known state. Replicating means generating my own version of the same primary
analysis.

The harm is not merely wasted effort. Generating the finding myself destroys the
independence that makes an audit worth anything, demotes the specialist to a rubber
stamp, and hides stalls by making steward activity look like project progress.

Narrow exception: spot-checking ONE number to verify a claim already made, bounded and
logged as verification.

## "Dead" is almost always an overclaim

Killing an APPROACH is cheap and good. Killing a GOAL needs a provable hard wall:
physics, mathematics, or a documented exhaustive search. Never "our implementation lost
money" or "I ran out of ideas."

Before writing that something is dead, state what would have to be PROVEN for the goal
to be impossible. If that statement is obviously unprovable, then an approach died, not
the project, and say so in exactly those words.

Say **COLD**, not dead: shelved, re-openable, awaiting better models or data. Record the
re-open condition explicitly. A materially more capable model is always a revival
trigger on every cold case.

An underpowered or undecidable result is NOT a kill either. Report the sample size that
would decide it and put that on the roadmap.

### The narrowing kill

Specialists find a narrow band of a broad thesis, prove that band fails, and report the
whole thesis dead without explaining the positive observation that started the project.

Before accepting any kill:

1. What was asked versus what the work order actually measured? The work order is always
   narrower. What is in the gap?
2. Does the negative EXPLAIN the motivating observation? If something demonstrably
   worked and the verdict does not say how, the verdict is incomplete: a failed
   explanation, not a kill.
3. Is there a footnote contradicting the headline? That is usually the actual result.
   Promote it.
4. Does the verdict state what died, what did NOT die, and what it implies next?

Audit scope before rigor. A perfectly rigorous answer to the wrong question is worse
than a sloppy answer to the right one, because it terminates inquiry with confidence.

**A project is not a hypothesis to be falsified. It is an anomaly to be explained.**

## The channel holds state, not history

This is the rule that makes the whole system readable, and it is the one users notice
first.

Scheduled agents append. A pass finds something, posts it, moves on. After a day the
channel holds a day of messages and reading it means replaying the agent's entire
thought process in order. Measured: 37 messages and 43,911 characters in one "brief"
channel in 24 hours, fifteen of them raw job-status posts. By the time the human reached
the bottom, most items had already been resolved by later passes.

Posting less is not the fix, because the individual items were real. **The channel
should hold what is true right now.**

`scripts/living_board.py` maintains ONE pinned message per board, edited in place:

```bash
living_board.py show    --topic brief
living_board.py set     --topic brief --title "Short human title" --file item.md
living_board.py resolve --topic needs-me --item "substring of title"
```

Rules that make it work:

- **Resolve before you add.** Every pass, read the current board and resolve every item
  your own work has since answered. A pass that only ever adds rebuilds the wall.
- **Titles are stable and human.** Matching is by title, so re-surfacing the same
  concern updates the existing item instead of duplicating it. A reworded title creates
  a second item. Never use internal codes or ticket ids in anything a human reads.
- **Resolved items disappear**, they are not struck through. A resolved item left
  visible is still something to read and dismiss.
- **Items are capped at 600 characters and the cap is enforced**, not advised. The first
  real pass after deployment wrote a 3,287-character item that pushed the board to 3,647
  of a 4,096 hard cap; one more finding would have truncated it. Long reasoning belongs
  in the project log. A prompt asking for brevity does not survive contact with an agent
  that just did something interesting.
- **Two boards, not one.** A decision board that should almost always read "Nothing
  needs you", and a change board for things worth knowing that need no action. The
  decision board's entire value is its emptiness.

Point the scheduled job's delivery at `local` so the job's own output is not ALSO posted
as a fresh message. The board is the delivery.

Setup: copy `templates/board.toml` to `~/.hermes/board.toml`, fill in the chat and topic
ids, export the bot token. The bot needs permission to send, edit, and pin its own
messages. Runs on Python 3.9+ with no dependencies.

## Gate spending on cluster health, not just quota

Two DIFFERENT questions, and both must be green before an expensive pass:

| Question                       | Answered by                   |
| ------------------------------ | ----------------------------- |
| Does the model pool have ROOM? | quota or capacity signal      |
| Is the model pool ANSWERING?   | error rate on recent requests |

A cluster can be at full quota and completely broken at the same time, and that is the
expensive combination, because every failure silently retries or fails over to a metered
model.

The incident that produced this rule: a primary model began throwing stream-abort
errors, heavily clustered inside a single hour, and every failure fell through to a
metered fallback. The retry storm burned two orders of magnitude more input tokens than
the output it produced, on a day that cost several times baseline. The capacity signal
reported healthy throughout and was telling the truth: it measures room, not liveness.
The scheduler marked every run `completed`. Nothing looked wrong from the inside.

**The tell for a retry storm is a huge input-to-output token ratio, not a high request
count.**

Implement the gate as a pre-run script rather than a prompt instruction, because reading
a prompt instruction is itself a model call. If your scheduler supports a wake gate (a
script whose output can suppress the run entirely), use it: that skips the model call
completely. Fail safe, treating an unreachable health source as degraded, because not
knowing is not the same as healthy and the downside is asymmetric.

## Silence is a success

If a pass produced nothing worth a board change, output nothing. Frequent cadence is
only safe because silence is real. A steward that speaks every pass trains the principal
to stop reading.

Watch both extremes: never silent means the pass is manufacturing work; always silent
means it is not working.

## Every project carries a roadmap

Five ranked falsifiable items, each with a kill rule, an executor, and a cost. A project
without one is invisible to the dispatcher: before roadmaps existed, two projects took
eight of thirteen passes and five starved.

When a pass has no obvious next action, pull the top open item. That is what replaces
"I'm stuck" and "this is dead."

## Fairness needs mechanism, not intention

Two guards, both learned from failures:

- **An atomic claim lock.** Two overlapping passes once issued the same work order
  twice. Create a lock directory per project before dispatch; skip a project that is
  claimed; treat a claim as stale only after a timeout AND proof the owning pass is
  dead.
- **A mechanical starvation floor.** Enumerate every project directory, find each one's
  last pass in the log, and treat never-picked as oldest. Do not rely on the model
  remembering which projects it has been neglecting.

## State lives in markdown, in plain English

One directory per project, holding a charter, a "now" file, an append-only log, a
roadmap, and a decisions file with re-open conditions. Prose, not JSON, not a database.

The reason is that the reader is a language model. State written as English can be
handed to a fresh model with "here is where this stands, continue" and it works. State
written as JSON has to be reconstructed into meaning first. Reserve a database for
high-volume mechanical lookups, not for the objects you reason about.

Keep an append-only record of dead ends, so a future pass does not re-propose a buried
idea without saying why the burial no longer holds.

## Judgment belongs to the model, plumbing belongs to code

The tell that this is being violated: writing a dictionary that maps a situation to a
decision. Threshold tables, tier maps, keyword classifiers.

Scripts do file reads, atomic writes, locks, and log appends. Anything requiring a read
of what a message MEANS is a model call. It is worth spending tokens to reason about the
next action, because that handles edge cases a lookup table cannot.

## Pitfalls

- **A self-editing prompt reverting its own fixes.** If the steward can rewrite its own
  prompt, it will eventually rewrite it from memory and silently drop recent changes.
  Verified: a pass dropped four just-applied fixes while adding good new doctrine, with
  no error. Keep an on-disk mirror, diff before editing, merge both directions rather
  than overwriting, and carry an explicit preservation clause naming the sections that
  must survive an edit.
- **A hardcoded list that stops matching reality.** The same defect appeared twice in
  one hour: lane clocks tuned for one project, and a weekly review job naming three
  project paths when the portfolio held thirteen, silently exempting ten. Enumerate
  directories; never hardcode a project list.
- **`completed` status meaning nothing.** A scheduler marking a run complete means the
  process exited, not that the work was sane or affordable.
- **Testing guards only in the passing direction.** Three separate bugs in the health
  gate passed a happy-path test and were worthless in the exact case they existed for,
  including one that made the gate silently always pass. Test every guard in the FAILING
  direction.
- **Deleting history to test an API.** An early board version grew a delete command that
  was used to probe the API against real messages, destroying four. The shipped version
  has no delete: the board converges the channel going forward, which is enough.
- **Changing something the principal owns without asking.** Group membership,
  permissions, another agent's config, shared infrastructure. Reversibility is not the
  test. It is still their call, however obviously correct the change looks.

## Reference files

- `references/lenses.md`, full advance / zoom-out / creativity definitions and triggers
- `references/portfolio-dispatch.md`, slot allocation, claim locks, starvation floor
- `references/project-state.md`, the markdown files each project carries, with templates
- `templates/board.toml`, living board configuration
- `scripts/living_board.py`, the board itself, Python 3.9+, no dependencies
- `scripts/verify_board.py`, offline self-check for the board; run it after editing it
  or on a new install. No token needed, no messages sent.
