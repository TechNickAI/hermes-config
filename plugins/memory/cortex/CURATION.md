# Memory curation

Tools that keep a Cortex knowledge store healthy over time: deduping, linking, enriching
metadata, splitting oversized pages, detecting junk, and escalating the small number of
judgment calls that genuinely need a human.

## Why

An audit of a long-running store found the maintenance job had quietly degraded into a
lint counter:

- It reloaded the **entire corpus every run** with no cursor, so a timeout meant
  re-processing the same early pages forever and never reaching the rest.
- Its link stitcher was a no-op (`Stitched 0 cross-refs across 0 pages`).
- It appended the same lint summary to a review queue every night. That queue reached
  **70 items, 80% stale, oldest ~3.5 months** — and **68 of 70 were automated lint
  spam**, burying the two real judgment calls.
- It reported success even when its interpreter path no longer existed.

Meanwhile the store itself drifted: **97% orphan rate**, 9% of pages carrying any link,
28 oversized pages (largest 167KB), and 95 files with dates baked into their filenames.

## Components

| Script               | Purpose                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------- |
| `review_queue.py`    | Deduped, lifecycle-managed queue. Content-hashed IDs, severity routing, TTL expiry, escalation caps.    |
| `weekly_curation.py` | Incremental curation pass with a checkpoint, so an interrupted run resumes instead of restarting.       |
| `transforms.py`      | The transforms that actually rewrite pages: derename, enrich, link, split, temporal annotate.           |
| `junk_detector.py`   | Finds files that do not belong in a knowledge store (build output, vendored bundles, archives, caches). |
| `curate_run.py`      | Runs the full transform suite against a **copy** and reports before/after metrics.                      |
| `memory_channel.py`  | Creates a per-agent "Memory Management" forum topic and posts escalations there.                        |

## Design rules

**Escalate only what needs a human.** Severity is enforced structurally:

- `needs_human` — contradictions, ambiguous merges. Escalated. Never auto-expires.
- `agent` — duplicates, oversized pages. Fixed by the curation pass.
- `info` — metrics. **Cannot reach a human.** Auto-expires.

**The queue must drain.** Every item ends as resolved, escalated, or expired. Re-raising
an open issue bumps a counter rather than creating a duplicate — the specific bug that
produced 68 identical entries.

**Dates belong in frontmatter, not filenames.** A filename should describe content.
`derename` moves the date into `date:`/`created:` and rewrites inbound links. Journal
directories (`daily/`, `journal/`) are exempt, since there the date _is_ the identity.

**Never guess destructively.** Deduping normalizes only timestamps and explicit counter
idioms. An earlier, looser version also normalized bare integers and proposed deleting
52 distinct journal stubs whose only differing content was numeric — caught because the
run was against a copy.

**Report, don't delete.** The junk detector has no delete path. Cleanup is a human
decision.

**Path containment.** Plan entries are treated as untrusted data: absolute paths, `..`
traversal, and symlinked components are refused, so a malformed plan cannot rewrite
files outside the store.

## Known limits

Stated plainly, because the previous system's failure was overclaiming:

- **Contradiction detection is a keyword pre-filter, not a detector.** It matches
  supersession language ("previously", "moved to", "instead") to narrow hundreds of
  pages to a readable handful. Precision is low by design; the LLM pass makes the actual
  judgment. It does not understand claims.
- **The temporal transform only handles narrow, well-formed claims** (endpoints, ports,
  models, locations, employers) appearing in dated sections, and requires conflicting
  values on different dates. It will miss most real contradictions. It is tuned to
  produce no false "Current state" banners rather than to catch everything.
- **Frontmatter round-trips through PyYAML** when available. Comments and anchors in
  frontmatter are not preserved.
- **No concurrency control.** Two curation passes against the same store at the same
  time can interleave writes. Run one at a time.

## Usage

Dry-run the full suite against a copy (live store untouched):

```bash
python scripts/curate_run.py --store ~/path/to/store --label mystore \
    --json-out /tmp/report.json
```

Incremental curation brief, and applying decisions:

```bash
python scripts/weekly_curation.py --store ~/path/to/store --brief
python scripts/weekly_curation.py --store ~/path/to/store --apply decisions.json
python scripts/weekly_curation.py --store ~/path/to/store --status
```

Junk scan:

```bash
python scripts/junk_detector.py --store ~/path/to/store
```

## Measured effect

Running the transform suite against copies of three real stores:

| Metric               | Store A    | Store B    | Store C   |
| -------------------- | ---------- | ---------- | --------- |
| frontmatter coverage | 94% → 100% | 69% → 100% | 8% → 100% |
| pages carrying links | 9% → 92%   | 10% → 98%  | 1% → 83%  |
| orphan rate          | 97% → 58%  | 97% → 57%  | 99% → 72% |
| date-named files     | 95 → 22    | 75 → 22    | 70 → 3    |

Body-prose word count changed by **+0.05%** — the transforms restructure without losing
content.

## Cadence

Weekly, not nightly. Curation requires reasoning, and a nightly cadence produced more
noise than a human would ever read. Cheap deterministic integrity checks can still run
daily; the LLM-driven pass runs once a week and reports to the agent's Memory Management
topic.
