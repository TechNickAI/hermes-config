# Reviewing threshold-based financial alerts

Use this reference for review-only checks of stock, crypto, or market-move alerts whose
publication is controlled by explicit trigger rules.

## Minimum evidence checks

1. Recompute the signed move from raw price and prior-close fields:
   `((current - previous_close) / previous_close) * 100`.
2. Verify the unrounded value crosses the rule threshold; never decide eligibility from
   the displayed rounded percentage alone.
3. Check every displayed transformation independently:
   - price and prior close,
   - high/low order and rounding,
   - abbreviated volume,
   - symbol, currency, and exchange,
   - source timestamp and market timezone.
4. Convert machine timestamps to the exchange timezone when freshness matters. For an
   intraday quote, include a concise `as of` time so the alert cannot be mistaken for an
   end-of-day result or a perpetually current quote.
5. Distinguish source adequacy from source preference. A documented fallback can be
   acceptable when its fields directly support the alert; do not imply the preferred
   source succeeded.
6. Verify the destination URL identifies the intended symbol and exchange rather than
   merely linking to a generic finance home page.

## Rules-compliance checks

- Treat direction and signed move as separate requirements when the rule names both.
  Prefer an explicit word such as `UP` or `DOWN` plus the signed percentage rather than
  relying on `+` or `−` alone.
- Confirm per-direction or per-day deduplication/slot state from supplied workflow
  evidence.
- Require all mandated context fields and the trigger reason.
- Respect review-only scope: propose the complete corrected alert when the verdict is
  `edit`, but do not send, persist, or mutate external state.
- Use exactly one mechanical verdict:
  - `pass`: no open material issue;
  - `edit`: straightforward correction, no judgment needed;
  - `hold`: missing evidence or a human choice is required;
  - `block`: unsafe, materially wrong, or rule-violating.

## Audience and voice

For a busy investor, optimize for mobile scanability without sacrificing provenance:

- Lead with symbol, explicit direction, signed move, current price, and quote time.
- Stay factual and calm; a threshold crossing is not automatically an emergency.
- Keep supporting context on one compact line where possible.
- Preserve conventional financial notation and enough precision to support trust.
  Display rounding may be concise, but the review must use raw values.

## Compact review output

When the caller requests lens-specific findings, return only:

1. one verdict;
2. key findings under evidence, empathy, rules compliance, and voice/audience;
3. the complete corrected alert if and only if the verdict is `edit`;
4. a brief confirmation that nothing was sent or changed when non-action was an explicit
   constraint.

Do not add panel mechanics, model names, process narration, or multiple competing
verdicts unless requested.
