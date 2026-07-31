# Project state

One directory per project. All markdown, all plain English prose.

```
projects/
├── portfolio.md          the principal's own briefing on what matters (they own this)
├── portfolio-log.md      one line per pass, append-only
├── entities.md           alias map: which agent owns what, and name collisions
└── <project-name>/
    ├── charter.md        what winning looks like, and the kill criteria
    ├── now.md            where this stands (REWRITTEN each pass, never appended)
    ├── log.md            append-only history
    ├── roadmap.md        five ranked falsifiable items
    ├── decisions.md      decisions with their re-open conditions
    └── dead-ends.md      what was tried and why it failed
```

## Why prose, not JSON or a database

The reader is a language model. State written as English can be handed to a fresh model
with "here is where this stands, continue" and it works. State written as JSON has to be
reconstructed into meaning first, and the reconstruction is lossy in exactly the places
that matter, like why a decision was made.

Reserve a database for high-volume mechanical lookups (thousands of yes/no dedup
checks). For the few dozen objects an agent actually reasons about, markdown wins.

Filenames stay lowercase. No human names files in capitals.

## charter.md

What the project is for, stated so that a stranger could tell whether it succeeded.

- The goal in one sentence, in the principal's own words where possible
- What winning looks like, concretely enough to be falsifiable
- The kill criteria, written BEFORE any measurement
- Who the domain agent is, and where their work lives
- What authority exists: money, external communication, irreversible actions

A charter with two independent promises is the most common source of silent
half-abandonment. Name them separately so a zoom-out can check each one's dispatch
history.

## now.md

Where this stands, right now, in about a page. **Rewritten each pass, never appended.**
The moment it becomes a log it stops being readable, and a ninety-line "now" file is a
sign the findings belong in the log instead.

- Current state in a few sentences
- What changed most recently
- The next action, and who owns it
- Any real blocker, with the specific decision it needs

## roadmap.md

Five ranked falsifiable items. Each carries:

- the question it answers
- the test, concretely
- a kill rule fixed in advance
- the executor
- the cost

Ranked by file order. A project without a roadmap is invisible to the dispatcher: it is
what a pass pulls from when there is no obvious next action, and it is what replaces
"I'm stuck."

Watch the ratio of items added to items closed. A project accumulating items faster than
it retires them has stopped testing and started planning.

## decisions.md

Every decision, with the condition that would re-open it. A decision without a re-open
condition is indistinguishable from a rule, and rules accumulate until nobody remembers
which were deliberate.

## dead-ends.md

Append-only. What was tried, what happened, and precisely what it does and does not
prove.

This is what lets a creativity pass avoid re-proposing a buried idea, and what lets a
new model be pointed at the project and told "try again" without repeating known
failures.

## portfolio.md

The principal's own briefing: what matters, roughly how often, and who owes the next
move. **They own this file.** The steward reads it as guidance and reports where reality
differs, rather than rewriting it.

## portfolio-log.md

One line per pass, newest at the bottom:

```
<timestamp>, <picked|skipped> <project> [<lens>], <one line why>
```

The lens tag is required. Rotation is tracked by reading this log, so an untagged line
makes a project look like it has never been zoomed out and corrupts the rotation.

## entities.md

The alias map. Which agent owns which project, which chat each lives in, and every known
name collision.

This exists because ownership is NOT inferable from subject matter. Two unrelated
projects can share a word, one of them carrying real money. A steward once wrote a work
order to the wrong agent on exactly such a collision. Enumerate and check before
addressing anyone.

## Retirement

Retired projects move to a `_retired/<name>-<date>/` directory with a note explaining
the verdict. They are never deleted. A cold project is re-openable, and a materially
more capable model is always a revival trigger.
