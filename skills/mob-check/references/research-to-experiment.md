# Turning crowd research into testable experiments

Use this when mob-check supports strategy R&D, signal discovery, or operating-system
design rather than a one-off sentiment brief.

## Evidence ladder

Keep three evidence classes separate:

1. **Primary research**: papers, filings, official datasets. Extract the measured
   variable, horizon, sample, and limitation.
2. **Practitioner observations**: Reddit, Hacker News, X, forums. Treat these as
   workflow hypotheses, not proof of edge.
3. **Tooling examples**: GitHub projects and vendor pages. Evidence that a pipeline is
   feasible, not that it is profitable.

Do not average these into one vague consensus. State where they converge and where they
do not.

## Convert findings into experiments

A useful backlog item names:

- the point-in-time feature to record,
- the baseline and comparison buckets,
- the outcome horizon,
- minimum sample size or review trigger,
- no-lookahead and idempotency fixtures,
- the rule that must remain unchanged while evidence accrues.

Prefer measurement-only work first. Do not hot-edit scoring, sizing, or gates from
literature or anecdotes.

For social-market research, distinguish:

- raw sentiment,
- attention level,
- attention or engagement velocity,
- cross-source propagation and lead-lag,
- source or caller quality reconstructed only from prior observations.

Fast propagation may mean confirmation, crowding, or faster price incorporation.
Pre-register lag buckets and evaluate all three possibilities.

## Artifact discipline

When saving ranker input or output:

- Preserve real URLs and stable unique input IDs.
- Leave unknown engagement fields empty. Never infer counts from qualitative wording.
- Record source-class coverage separately from ranked-source coverage.
- Read `thin_evidence` and `low_engagement_coverage` as different warnings.
- Remember that `scripts/rank.py` does not echo input IDs; join ranked results back by
  unique URL.

After writing a JSON research artifact, perform focused ad-hoc verification if no
canonical suite owns it:

1. Parse the exact saved file.
2. Assert item count, unique IDs, required fields, source classes, and URL shape.
3. Pipe the parsed artifact through `scripts/rank.py` and assert the expected coverage
   invariants.
4. Use an OS-safe temporary verifier with a `hermes-verify-` prefix, propagate the
   subprocess exit code, and remove it afterward.
5. Report this as **ad-hoc verification**, not "suite green." A missing or unwritten
   verifier is not a pass.

## Strategy-R&D output

Deliver the decision value, not a social-media digest:

- strongest actionable convergence,
- concrete experiment(s) promoted,
- one number that constrains confidence,
- explicit next build step,
- citations or artifact paths.

Avoid treating loud discussion as representative or predictive merely because it ranked
highly.
