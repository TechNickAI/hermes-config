# Reviewing a skill you just wrote: what the panel actually catches

Recorded after a 5-seat cross-family review of newly authored Kalshi skills. The seats
found real defects in code I had already self-tested and shipped. These are the patterns
worth carrying forward.

## The guard that reproduces the bug it exists to prevent

The single highest-value finding class. An oracle written to stop YES/NO axis confusion
had `price_c_on_axis(payload, outcome_side=None)` falling back to
`read_direction(payload)`. But the skill's own governing fact is that the payload CANNOT
distinguish a NO-entry from a YES-exit — so the default path confidently returned the
complement of our cost on every exit.

**Rule: for every safety helper, ask "what does this do when called the lazy way?"** If
the convenient call signature produces the error the file was written to prevent, the
argument must be REQUIRED, not defaulted. A self-test that only exercises the explicit
call stays green forever.

## Vocabulary collision inside your own function signature

`yes_leg_cash_c(book_side, ...)` accepted `"sell"` as an alias for `"ask"`. But Kalshi's
own table maps `sell + no -> book_side=bid`. So passing `fill["action"]` into a
parameter named `book_side` inverted the sign of the cash — in a file whose entire
thesis is "action is not a direction."

**Rule: a parameter named for one vocabulary must REFUSE values from another.** Aliasing
them "for convenience" re-creates the confusion the module documents.

## Circular tests: measuring an identity and calling it a finding

A "complement pricing adds no information" kill rested on comparing `own_mid` against
`1 - sum(other_mids)`. That difference is algebraically `sum(all_mids) - 1` —
**identical for every leg in the event**. The measurement could never have detected
predictive value; the flat result was baked in.

**Before believing a negative, write the metric out symbolically and check it can vary
independently of what it claims to test.** A result that is constant by construction is
not evidence. Verify by computing per-leg values: if they are all the same number, you
measured an identity.

## Resolution floors masquerading as measurements

A median gap of "exactly 0.500c in both buckets" looked like a clean null. On a 1c tick,
mids land on a 0.5c grid, so 0.5c is the SMALLEST NON-ZERO VALUE the instrument can
emit. The median was pinned to the floor.

**Rule: whenever a statistic equals the minimum representable value, suspect the
instrument.** Print the full distribution of observed values; if they are quantized and
your statistic sits on the first quantum, say "within one tick," not a precise number.

## Normalization is not calibration

"Legs sum to 1.0000, so the market is priced fairly" is wrong. Quotes of 0.20/0.80 sum
to one even when true probabilities are 0.10/0.90 — which IS favorite-longshot bias.
Sum-to-one says the quotes are internally consistent and nothing about whether they are
right.

Testing calibration requires OUTCOMES (settlement, or forward markouts at the strategy's
real holding horizon), clustered by event, out of sample.

## Rule of three on any zero

"0 violations in 959 rungs" has a 95% upper bound of ~3/959 = 0.31%. Also check
independence: 959 rungs inside ~93 ladders at ONE instant is not 959 draws, and a
pre-game snapshot says nothing about in-play windows. Scope every zero to its sample,
its clustering, and its regime.

## Gates that no real edge can pass

An imported rule required a strategy to "beat baseline on each individual day."
Computed: a daily-Sharpe-1.0 strategy (exceptional) fails an all-30-day gate **99.4%**
of the time; at Sharpe 0.5 over 20 days, P(pass) = 6.2e-04.

**Before adopting a validation gate, compute its false-negative rate against a
known-positive process.** A gate with a ~100% false-negative rate looks rigorous and
only ever produces "no." This is the abstention-as-safety failure mode wearing a lab
coat.

## Keep the instrument that was once wrong

A ladder probe's v1 reported 172 fabricated arbitrages (it interleaved two teams'
ladders inside one event). The corrected v2's numbers were written into a doc and the
script was discarded — leaving an unreproducible claim from a tool with a DEMONSTRATED
failure mode.

**A measurement whose instrument had a known bug must ship WITH the corrected
instrument.** Re-run it rather than citing the stored number; string-heuristic parsers
silently break when the venue renames things.

## Verify unit claims on the live board, not from the ticker

A threshold contract `strike_type=less, cap_strike=85` is titled **"84° or below"** —
STRICT, not inclusive. Treating it as inclusive double-counted the strike against the
neighbouring bracket and made an integer partition sum to 0.8554 instead of 1.0. The fix
came from reading `yes_sub_title` on the live board, which states the semantics in
English.

**Partition sums are the strongest available self-check for bracket math: assert MECE ==
1.0 in the self-test, on a REAL board shape.** A hand-invented ladder will encode your
own misunderstanding and pass.

## Sabotage tests can be inert

Two sabotages "passed" (selftest stayed green) not because tests were weak but because
an EARLIER guard caught the mutation first. Rewriting them to remove the upstream guard
as well made both bite.

**A surviving sabotage means one of: weak test, inert sabotage, or a different guard
fired.** Distinguish before concluding. Always `diff -q` to prove the file actually
changed.

## Before writing a "definitive" skill, find the production implementation

The most damaging finding in a five-seat panel was structural, not a bug: a skill titled
_"STOP re-deriving this"_ was itself a **second derivation**, sitting next to a
battle-tested live decoder (`position_manager/fill_provenance.py::decode_axis`) that it
never mentioned. The teaching artifact was also the LOOSER parser — production raised on
non-complementary axes; the skill silently returned a number.

**Rule: grep for the existing implementation BEFORE authoring doctrine.** A skill that
competes with production code guarantees drift and invites an agent to copy the weaker
version into a money path. Name the production entry point at the top, say explicitly
"if you are touching money, call THAT," and scope the skill's own code to explaining and
sandboxing.

Corollary: when a skill bans a field, check how production uses it. Banning `action` as
a DIRECTION is right; production also uses `action` as the YES-leg VERB to mirror once
the axis is known. A blanket ban would have broken the live NO-close path.

## Make the wrong call impossible, not merely documented

A guard requiring an explicit axis argument still permitted the natural composition
`price_c_on_axis(fill, read_direction(fill))` — which returns the payload's own side and
reproduces the exact bug the function existed to prevent. A docstring saying "this must
come from provenance" cannot stop a string.

**Rule: if a parameter must come from a specific SOURCE, give it a TYPE that only that
source can construct.** A `RecordedAxis(side, source="...")` wrapper turns the wrong
call into a TypeError instead of a wrong number. Ask of every safety argument: "can the
caller satisfy this from data I just told them not to trust?"

## Look for the MIRROR of every asymmetric trap

The skill documented at length that a NO-entry and a YES-exit are byte-identical on the
ASK side. The BID-side mirror — a YES-entry and a NO-exit, equally identical — was never
written down, and its diagnostic table had a row for one direction only. A parser that
"learned the lesson" on one side still mislabels every row on the other.

**Rule: when you document a trap on one side of a symmetric system, explicitly state the
other side — even to say it is safe.** Silence reads as "handled."

## An overclaimed universal rule breaks a correct exception

"Never take the axis from the payload; always join to provenance" was stated as
universal. But `bond/reconcile.py` correctly prices straight off `outcome_side`, because
a bond reservation is ALWAYS an entry — a channel invariant IS provenance. An agent
"fixing" that to a ledger join would fail closed in exactly the window reconcile exists
to cover.

**Rule: a rule strong enough to be followed blindly must name its exceptions.** Write
the invariant down rather than letting a future agent delete it as a bug.

## Don't let a fresh measurement overturn a documented caveat

I asserted "levels are ascending, take the LAST element" from 10 sampled books. A
sibling skill documented that the ordering is NOT guaranteed. Ten samples cannot
overturn that, and `max()` is correct under both readings at zero cost.

**Rule: when your new measurement contradicts an existing skill, reconcile them
explicitly rather than shipping the more confident claim.** Two auto-loading skills that
teach opposite procedures is a load-order bug, and the newer file's confidence is the
defect.

## Close scope gaps by measuring, not by caveating

A seat noted the census covered subaccounts 0/5/6/7 and not 1-4. Rather than adding a
hedge sentence, I ran the sweep: 296 more rows, zero missing canonical fields. **A
caveat you can convert into a measurement in five minutes should be measured.** Reserve
caveats for what genuinely cannot be checked (there, the WebSocket delta path).

## Panel mechanics

- Read EVERY seat end to end. Reviewers order findings by section, not severity; the
  tail holds real defects.
- Check `rc` per seat: 127 = never ran, 143/137 = killed, 0 + bytes = real.
- A seat running 3x longer than its siblings with 0 bytes is hung — kill it and proceed.
  Four cross-family seats is a valid panel.
- Kill the RUNNER script before individual seats, or a sequential panel respawns them as
  fast as you kill them.
- Verify findings before acting: run the actual predicate. Two of the strongest-
  sounding findings were already handled; several checkable ones were exactly right.
  Neither deference nor dismissal — execute the check.
