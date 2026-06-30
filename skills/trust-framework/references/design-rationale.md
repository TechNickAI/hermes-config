# Design Rationale

Why the trust framework is shaped the way it is. Read this when adapting it or when a
design choice feels arbitrary.

## Why two layers (doctrine + enforcement)

A governance rule that lives only in a prompt is theater. A capable model can
rationalize past any rule it can merely read — "this delete is probably fine, it's
basically reversible." So the framework splits cleanly:

- **Doctrine (prompt):** judgment the model is good at — reading a situation,
  classifying risk, weighing reversibility. This belongs to the model.
- **Enforcement (code + ledger):** the few things the model must not be able to talk
  past — who resolves outcomes, how promotion math runs, what the hard caps are. These
  live in a non-agent script and a config file the agent cannot self-edit.

If you only ship the doctrine, you have behavioral guidance and an honest L1-everywhere
posture — useful, but not a safety system. The enforcement layer is what makes earned
autonomy real.

## Why per-bucket, not one global trust score

An employee is trusted with the inbox long before the bank account. A single global
"trust level" forces a bad tradeoff: either too cautious on safe work or too loose on
dangerous work. Per-bucket trust lets an agent be L3 on drafting while still L1 on money
— which is exactly the right shape. It also kills the easy-success-farming exploit at
the design level: you cannot rack up trivial wins in one bucket to earn authority in
another.

## Why the agent never grades its own homework

This is the single most important property. If an agent can write its own `outcome`
field, the entire trust ledger is fiction — it defaults everything to `success` and
promotes itself on a fabricated record. Multi-model review of an early draft converged
hard on this as the load-bearing crack.

The fix: the agent writes the _decision_ and its _classification_ (both auditable
claims), but the `outcome` is resolved **only from external signals** (human reactions,
replies, corrections, reverts) by a non-agent script. Silence is not success — an
unresolved entry stays `pending` and never counts. And the classification itself is
audited: a human correcting a bucket/door call counts as an error even when the action
was harmless, because the classification is the safety judgment, and getting lucky is
not getting it right.

## Why reversibility is the primary axis

Borrowed directly from the one-way/two-way door heuristic. Reversibility is the cleanest
predictor of how much a mistake costs: a two-way-door error is a cheap correction, a
one-way-door error is permanent. Confidence is secondary — a one-way door at 99%
confidence still escalates if it's above the agent's earned level, because the rare miss
is unrecoverable. Blast radius is the multiplier: reach turns a nominally-reversible
action one-way when the harm lands before the undo can.

## Why demotion is automatic and unsentimental

Trust is non-monotonic. Humans hesitate to revoke trust (sunk cost, not wanting to
offend); an error budget doesn't. Tying demotion to a measured budget breach removes the
lag and the emotion — the agent drops a level the moment its record says it should, and
re-earns it with fresh successes. Disuse decay handles the "trusted two years ago, idle
since" case: stale trust is not live trust.

## Why the human keeps the meta

An agent can earn autonomy _within_ the framework, but never rewrites the framework, its
own caps, or its promotion criteria. Self-modification of governance is the ultimate
one-way door — an agent that can lower its own bar has no bar. Promotions into
_higher-risk_ buckets are escalated to the human for the same reason: nothing
self-grants more dangerous power.

## Why it defers to the host's own approval layer

The framework is the judgment layer — it decides _whether to attempt_ an action. If the
runtime also has an execution gate (a command-approval mode, a tool allowlist), earning
L3 does not bypass it; the agent surfaces a host-level block instead of hallucinating
success. The two compose: judgment up front, a backstop on execution. Belt and
suspenders on the actions that matter.

## Why the ritual is tiered to risk

A mandatory five-question block on every trivial action does two bad things: it burns
tokens, and it trains the human to rubber-stamp a flood of micro-escalations (automation
bias by exhaustion). So routine reversible low-risk actions need only a one-line log;
the full block is reserved for medium+ risk and one-way doors, where the deliberation
actually earns its cost.
