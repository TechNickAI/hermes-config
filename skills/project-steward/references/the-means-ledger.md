# The means ledger

The inventory of what a project can actually use: data sources, capital, tools, people,
budgets, and the constraints that are real versus the ones an agent invented.

## The problem it solves

An autonomous steward reads project files. The principal holds a large amount of
relevant state that is in no file: which vendors they will pay for, which capital has
which restrictions, who they know, what a partner's tax residency implies, which
comparison base is exhausted. That state arrives as surprise corrections in chat and
then evaporates.

Measured on one portfolio: of eleven principal interventions across five project
threads, **four injected a resource that was not in the option set and three deleted a
constraint the agent had adopted as physics.** Seven of eleven. Meanwhile the steward's
own messages averaged 2,641 characters and used statistical vocabulary 3.8 times per
message; the principal's averaged 147 characters and used it zero times.

The steward was reducing uncertainty inside a fixed option space. The principal was
expanding the space. Only one of those can find something that is not already in the
room.

**The generator, stated precisely:** an LLM cannot deduce a partner's citizenship or a
vendor's existence by reasoning harder. Absence of a resource is indistinguishable from
impossibility unless someone writes down what is available. So agents treat absence as
physics, and the project quietly scopes down around a limit that does not exist.

## Why a resources file rots

The obvious implementation is `resources.md`, and it will be stale within a week.
Nothing reads it, so nothing notices it is wrong, so nobody updates it.

**The forcing function must be on the READ, not the write.** A file that must be
consulted before an action can proceed stays alive, because being wrong blocks
something.

## Structure

`means.md` per project, plus a portfolio-level `means-portfolio.md` for cross-cutting
facts. Plain English, one line per item: what it is, its kind (data, capital, tool,
person, constraint, vendor), its bound or cap, when it was last touched, and where it
came from.

**No secrets.** Keys live in the environment or a password manager. The ledger records
that a key EXISTS and what it unlocks, never its value.

Constraints are first-class entries, and they carry a status: real, assumed, or
**voided**. A voided constraint is the highest-value row in the file, because it
retroactively invalidates every decision made under it.

## The four gates

1. **Pre-dispatch gate.** Every work order cites the means it assumes. An order that
   assumes a constraint the ledger records as voided is illegal to send. This is the
   gate that would have prevented most of the surprise corrections from being needed.
2. **Staleness lock.** If an active project's means have not been touched in fourteen
   days, the next pass must question the option set before it may verify anything,
   except when live money is at risk.
3. **Surprise-correction hook.** Any principal message that injects a resource or
   deletes a constraint patches the ledger **in that same turn, before any new order
   goes out.** If the patch did not happen, the nightly review fails the day. This is
   what converts a pasted API key into system state instead of a fact that dies in a
   thread.
4. **Negative space.** A pass that questions the option set must either add a means or
   record "searched X, found nothing." Silence is a failure, not a result.

## Constraint deletion is retroactive

When the principal deletes a constraint, the question is never "which pending decision
changes." It is **"what did we already reject under that constraint."**

Worked case: a scout project had rejected candidates under a citizenship filter and
benchmarked everything against an incumbent venue. The principal noted a non-US capital
source and that the incumbent had hit its concentration ceiling. The pending verdict was
the small consequence. The large one was that **every prior rejection had been scored
through a filter that was now void.**

Treat a voided constraint as a trigger to re-open the rejected set, not just to re-score
the live one.

## What this is not

Not a CRM, not a knowledge base, not a place to accumulate everything the principal has
ever said. It holds what a project could USE and what is genuinely blocking it. If an
entry cannot change a decision on some project, it does not belong here.
