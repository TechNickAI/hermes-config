# Design Rationale

Why the trust framework is shaped the way it is. Read when adapting it or when a design
choice feels arbitrary.

## Why a plain-markdown ledger, not a database or a YAML config

State and learning both live in one markdown file (`TRUST.md`) the agent reads and
appends to. Three reasons:

1. **Markdown is the native state medium here.** The agent and the human both read and
   edit markdown naturally; it rides the same memory/skill loop the agent already uses.
   A SQLite ledger or a YAML config would be bespoke infrastructure that needs a parser,
   a schema, and a background service to maintain — none of which exist, all of which
   can rot.
2. **Transparency is the integrity mechanism.** The whole point is that the human can
   audit the agent's self-governance at a glance. A plain-text levels table + decision
   log is readable and hand-editable in any editor. If the human disagrees with a level,
   they edit one table cell. You cannot get that from an opaque `.db`.
3. **No invented machinery.** An earlier draft proposed a `trust.db` plus a "non-agent
   resolver script" to settle outcomes. That's speculative infrastructure with no
   consumer — exactly the kind of thing to reject. The honest, working version is: the
   agent records reality in markdown and re-reads it. The accumulating log _is_ the
   memory; re-reading it and moving the levels _is_ the learning.

YAML in particular was the wrong call: it's a _config_ format, and this isn't config —
it's a living record the agent writes to and learns from. Config files aren't where you
keep an append-only history.

## How state and learning actually work

The loop is deliberately boring, because boring survives:

- **Log at action time** with `outcome: pending`.
- **Resolve later from observable reality** — did the human correct it, revert it, react
  well, or say nothing? Unresolved stays `pending` and never counts as a win. The agent
  records what happened, not what flatters it.
- **Review periodically:** read the log, tally each bucket against the promotion
  criteria, rewrite the levels table, note the change. Promotions and demotions both
  fall out of the same tally.
- **Human audits the markdown** anytime.

There is no learning step hidden in code. The learning is literally: write down what
happened, read it back, adjust the levels. That's it.

## Why per-bucket, not one global trust score

An employee is trusted with the inbox long before the bank account. A single global
level forces a bad tradeoff: too cautious on safe work or too loose on dangerous work.
Per-bucket trust lets the agent be L3 on drafting while still L1 on money — the right
shape. It also kills easy-success farming at the design level: you can't rack up trivial
wins in one bucket to earn authority in another.

## Why the agent records outcomes honestly (and the human can check)

If the agent could write a flattering `success` on everything, the ledger would be
fiction and self-promotion would be trivial. Two things keep it honest: (1) outcomes are
resolved from _observable_ signals — a correction, a revert, a reaction, or silence —
not from the agent's wish; unresolved entries never count; and (2) the whole log is
plain markdown the human reads, so a pattern of dishonest `success` marks is visible and
correctable. The classification itself is audited too: if the human corrects a
bucket/door call, that's an error even when the action was harmless, because the
classification is the safety judgment and getting lucky isn't getting it right.

## Why reversibility is the primary axis

Reversibility is the cleanest predictor of how much a mistake costs: a two-way-door
error is a cheap correction, a one-way-door error is permanent. Confidence is secondary
— a one-way door at 99% confidence still escalates if it's above the agent's level,
because the rare miss is unrecoverable. Blast radius is the multiplier: reach turns a
nominally-reversible action one-way when the harm lands before the undo can.

## Why demotion is automatic and unsentimental

Trust is non-monotonic. Humans hesitate to revoke trust; an error budget doesn't. Tying
demotion to a measured budget breach removes the lag and the emotion — the agent drops a
level the moment its record says it should, and re-earns it with fresh successes. Disuse
decay handles "trusted a while ago, idle since": stale trust isn't live trust.

## Why the agent never raises its own ceilings

The agent can earn freedom _within_ its buckets, but it never raises a bucket's ceiling
(e.g. letting itself start sending external comms or moving money) or rewrites the
framework itself. Self-granting dangerous new power is the ultimate one-way door — an
agent that can lift its own bar has no bar. Those changes are prepared and handed to the
human.

## Why it defers to the host's own approval layer

This framework is the judgment layer — it decides _whether to attempt_ an action. If the
runtime also has an execution gate (a command-approval mode, a tool allowlist), earning
L3 doesn't bypass it; the agent surfaces a host-level block instead of pretending
success. Judgment up front, a backstop on execution.

## Why the ritual is tiered to risk

A mandatory five-question block on every trivial action burns tokens and trains the
human to rubber-stamp a flood of micro-escalations (automation bias by exhaustion). So
routine reversible low-risk actions need only a one-line log; the full block is reserved
for medium+ risk and one-way doors, where the deliberation earns its cost.
