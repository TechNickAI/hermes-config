# Dispatching a review panel (native runtime)

`moa-solve` used to ship a `scripts/panel.py` that fanned a brief out to N model seats.
**That script is gone** (retired one occasion). MoA now runs on Hermes' native runtime,
and review panels use one of the two native paths below. Do not reintroduce a bespoke
dispatcher — it bypasses provider config, cost accounting, and credential handling.

## Two native paths

- **Independent reviewer one-shots** — the default for review work. Run each reviewer as
  its own `hermes -z` call with a different `--provider`/`-m`, so each lens gets its own
  mandate and the reviewers cannot anchor on each other. This is what replaces the old
  per-seat `mandate` field.

  ```bash
  hermes -z "$(cat review_brief.md)" --provider <alias> -m <model> -t '' --ignore-rules \
    > /tmp/rev_<lens>.md
  ```

  `-t ''` loads no toolsets (reviewers are text-only). `--ignore-rules` strips the
  calling profile's persona — **it also strips project privacy rules, so re-inject any
  privacy or PII constraints into the brief itself.**

- **A configured `moa:` preset** (`/moa`, or `--provider moa -m <preset>`) — when you
  want one synthesized answer rather than N separate reviews. Note the tradeoff: a MoA
  preset slot is only `{provider, model}`, so **there is no per-seat mandate**. Every
  reference sees the same brief. Put the lens structure in the brief and ask each
  reference to answer all of it.

## Choosing reviewers

Use different model FAMILIES, not three builds of one family. Pair each model with the
provider alias that genuinely fronts it; a mismatched alias can return HTTP 200 while
silently translating. Resolve model names from live config, never from a hardcoded slug.

## Verifying the panel actually ran

This is the part the old script got wrong and the reason the lesson survives: **a panel
can silently narrow while every signal says it ran at full width.**

- Running reviewers as separate one-shots: check each output file is non-empty and
  structurally complete. A reviewer that returned nothing is a missing lens, not a pass.
- Running a `moa:` preset: enable `moa.save_traces: true` and read
  `<hermes_home>/moa-traces/<session_id>.jsonl`. There is **no `error` key** — a failed
  slot records `[failed:...]` inside its `output`, a skipped one `[skipped:...]`.

Count the lenses you actually received before synthesizing. If fewer families answered
than intended, stamp the result `degraded: <n>-family` and say so.

## Durable pitfalls (carried over from the panel.py era)

- **A slow reviewer is not a dead one.** Reasoning-heavy models can take many minutes
  while siblings finish in one or two — and the slowest seat was sometimes the sharpest.
  Use a generous timeout (600s) and wait for completion before declaring a lens missing.
- **Short is not necessarily truncated.** Length disparity is a warning, not proof;
  check whether the response is _structurally_ incomplete (stops mid-section, never
  concludes) before discounting it. A genuinely concise review can be the best one.
- **Long is not necessarily substantive.** A very long response can be mostly visible
  chain-of-thought that never commits. Skim structure and the final section rather than
  reading it linearly into context.
