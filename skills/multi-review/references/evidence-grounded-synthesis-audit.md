# Evidence-grounded synthesis audits

Use this reference when reviewing a MoA synthesis, research synthesis, ranked bottleneck
list, strategy diagnosis, or any artifact that turns raw observations into prioritized
recommendations.

## Session lesson

A synthesis can be directionally useful while still over-claiming. In the one occasion
MoA bottleneck audit, the strongest failure mode was not a missing idea; it was treating
design docs, anecdotes, and partial DB counts as quantified ranked impact. The review
had to separate:

- **Documented bug/risk**: source docs or code show a flaw exists.
- **Observed frequency**: logs/DBs show how often it occurred.
- **Measured impact**: PnL, latency, fills, or safety outcomes quantify cost.
- **Addressable impact**: replay/A-B evidence shows the proposed fix captures that
  value.

Do not let a synthesis rank bottlenecks by narrative confidence when the evidence only
proves existence.

## Audit procedure

1. **Locate the actual synthesis and raw artifacts first.** Do not audit from memory or
   broad snippets if the source file/data can be read.
2. **Extract every ranked claim exactly**: rank, bottleneck name, claimed metric/impact,
   and proposed fix.
3. **Build an evidence matrix** for each claim:
   - primary source path/table/query;
   - line numbers or SQL counts;
   - evidence type: code, design doc, live DB, log, replay/backtest, experiment;
   - what it proves;
   - what it does _not_ prove.
4. **Distinguish risk from measured bottleneck.** A critical design bug is not
   automatically the top PnL bottleneck unless frequency and impact are measured.
5. **Check for causal leaps**:
   - no-fill -> capital allocation (could be queue, price, liquidity, stale scans,
     cancellations);
   - schedule/tick interval -> speed bottleneck (could be economic rule bug);
   - design-doc severity -> observed production loss;
   - backtest PnL -> live edge without no-lookahead/fill realism.
6. **Check denominator drift.** If local/live DB counts differ from provided numbers,
   report the queried path/time and avoid implying the raw numbers are immutable.
7. **Do not add overlapping dollar estimates.** If two fixes target the same
   missed-value pool, combined upside is capped by the pool, not the sum.
8. **Mark unsupported rankings explicitly** as `proven existence`, `measured frequency`,
   `measured impact`, or `unproven/theoretical`.
9. **End with kill-tests**: the smallest query, replay, A/B, or instrumentation that
   would validate or falsify each ranked bottleneck.

## Reporting shape

For each ranked bottleneck:

- **Claim**: quote/paraphrase the synthesis claim and rank.
- **Evidence found**: paths, lines, queries, counts.
- **Supported?** yes/partial/no, with reason.
- **Bad assumptions / missing evidence**: specific gaps.
- **Ranking verdict**: justified, plausible but unproven, over-ranked, under-ranked, or
  theoretical.
- **Next proof step**: concrete measurement or experiment.

Keep the final answer evidence-first and concise. Do not turn it into an implementation
plan unless the user asks for fixes.
