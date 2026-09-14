# Balancing the adversarial / bear lens so a panel still DECIDES

When a multi-review panel governs a **go / no-go decision** (place a trade, ship a risky
change, approve a spend), the red-team / bear / adversarial lens has a structural
failure mode Nick named directly (one occasion):

> "Don't just fall victim to always listening to the BEAR case or we will never trade.
> Find a way to balance that."

If you give N reviewers a mandate to find reasons NOT to act, all N will find at least
one — every real action has a plausible risk. A naive panel that lets any reviewer veto
therefore **rejects everything**, which is exactly as useless as approving everything.
An engine that never says "go" has failed the same way one that says "go" to junk has.

## The mechanism: the bear must EARN its weight

Do NOT vote-count and do NOT let the adversarial lens veto on pessimism. Instead score:

    decision = bull_strength
             - (weight of ONLY the valid bear points)
             - priced_in_penalty
             - (correctness_ok ? 0: hard_veto)

A bear/adversarial point counts toward the subtraction **only if it is ALL of**:

1. **Specific** — names a concrete mechanism, not "it could go down / this is risky."
2. **Falsifiable** — a checkable claim, not a vibe.
3. **Not already priced in** — the risk isn't already reflected in current state/price.
   (A "specific, falsifiable" risk that's already discounted gets NO free ride — this is
   the subtle case that keeps the bear honest.)
4. **Carries a disconfirmer** — the reviewer must state _what evidence would make this
   NOT a risk_. No disconfirmer ⇒ generic doom ⇒ **weight 0**.

Generic pessimism collapses to zero automatically. A real, unpriced, falsifiable risk
lowers conviction proportionally but cannot single-handedly kill the decision.

## HARD VETO is correctness-only, never caution

Reserve the one true veto for **correctness**, not risk-aversion:

- the signal isn't real / can't be verified,
- the artifact is structurally invalid (e.g. "activist 13D" whose filer is the company's
  own founder — insider, not activist),
- already-resolved / stale,
- a genuine safety/legal line (MNPI, secrets, irreversible harm).

Only the **verifier lens** (the seat that reads the actual source) may trigger it. A
pessimist lens cannot veto. In tests: a correctness hard-veto beats even a maximally
confident bull; a doom-only bear does not move the decision at all.

## Calibration guard: a collapsed pass-rate is a PROCESS ALARM

Track the pass-rate across a batch. If the panel passes ~nothing (target a healthy
fraction, e.g. 15-35% reach go/small-go), the **process is miscalibrated** — too-strict
judge or a bad candidate feed — and that must be surfaced as an alarm, NOT accepted as
"nothing qualified." Silent 0% pass looks like diligence and is actually a broken gate.

## Separate three questions so a "no" on one doesn't masquerade as a "no" on all

Give distinct lenses distinct jobs and keep their answers separate:

- **Is it real?** (verifier) — retrieval/classification correctness.
- **Is there money left?** (priced-in / market-structure) — has the move happened.
- **Should we act?** (bull vs valid-bear) — the decision itself. A candidate can be
  real, have upside left, and still be a PASS on specific unpriced risk — but "it might
  not work" only bites at the decision step, and only when specific.

## Where this was proven

Rumor Desk "court" (<agent-d>, one occasion): a 5-seat MoA panel (bull / bear /
priced-in / catalyst-verifier / disconfirmation-scout) + a synthesizing judge encoding
the rules above. Implemented in `scripts/rumor_desk/rumor_court.py` with
`build_role_prompts()` + `adjudicate()`; the anti-doom scoring is covered by durable
tests. Deep research on a 23-name book left only ~2 marginal survivors — the panel
correctly killed junk without the bear vetoing the whole book, and correctly PASSed a
real-but-priced-in merger (WBD) for honest reasons rather than pessimism.
