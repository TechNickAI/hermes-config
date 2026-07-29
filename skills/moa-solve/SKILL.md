---
name: moa-solve
description: >
  Use when you have a HARD, open-ended, high-stakes problem worth throwing multiple AI
  models at and pulling the best solution out — architecture decisions, strategy design,
  thorny debugging, research synthesis, "what am I missing", tool/system design. This is
  the SOLVE counterpart to multi-review (which critiques an existing artifact). It
  drives Hermes' NATIVE Mixture-of-Agents runtime (`/moa` and the `moa` virtual
  provider): a configured reference layer answers independently, then an aggregator
  writes a NEW best-of-breed answer via a component ledger — not an average, not a
  cut-and-paste splice. Also encodes WHEN NOT to use a panel (most problems), routing to
  a single strong model instead.
version: 2.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [orchestration, multi-model, moa, synthesis, problem-solving]
    related_skills: [multi-review, create-great-prompts]
---

# MOA-Solve — Mixture-of-Agents for hard problems

## What this is

A control plane for solving hard problems with multiple models, built on **Hermes' own
MoA runtime**. Grounded in Mixture-of-Agents (Wang et al., Together AI,
arXiv:2406.04692, ICLR 2025): a **reference layer** (several model families answer
independently) plus an **aggregator** (one strong model writes a NEW synthesized
answer). The paper's load-bearing result: the aggregator that _synthesizes_ beats an
LLM-ranker that merely _picks the best proposal_. That is the empirical basis for "pull
the best from EACH, don't pick the single best ONE."

Important reframing (do not skip): "best from each" does NOT mean cut-and-paste splicing
spans from references. Splicing produces Frankenstein artifacts where model A's
architecture assumes state that model B's grafted completion never provides. It means:
**synthesize a superior answer that is accountable to the strongest element found in
each reference**, grafted through explicit interface checks. The component ledger
(below) is how you do that without averaging to mush.

**This skill does not implement fan-out.** Hermes already ships a real MoA runtime with
parallel reference dispatch, per-slot provider/model routing, credential handling,
prompt-cache decoration, usage/cost accounting, and trace persistence. This skill is the
**method** — when to convene a panel, how to brief it, and how to synthesize the result.
Do not hand-roll an HTTP fan-out script; see "Why there is no runner script" below.

## When to use (and when NOT to — read this first)

The most common failure is running a panel when you shouldn't: you pay several times the
cost and latency for consensus noise on a problem where a single model was fine. Panels
earn their keep only when models _disagree_, and disagreement is rare on easy problems.

Routing rule:

- **Single strong model** — routine, low-stakes, or easily verifiable. This is the
  default for almost everything.
- **MoA panel (this skill)** — only when ALL hold: (a) high-stakes / irreversible /
  reusable, (b) genuinely open-ended (multiple defensible answers, no ground truth), AND
  (c) you expect real disagreement between model families.

Route on task SHAPE, never on a vendor's benchmark claim.

Trigger phrases: "throw a few models at this", "hard problem, want the best solution",
"what am I missing on X", "design/architect Y", "solve this properly", "MOA this",
"panel this".

## How to run a panel

Hermes exposes MoA three ways. All three read the same `moa:` config block, so the panel
composition is defined once in config and never hardcoded in a prompt.

### 1. Interactive one-shot — `/moa <prompt>`

Runs a single turn through the default preset, then restores the previous model. This is
the normal path when a human is in the loop.

### 2. Headless one-shot — the `moa` virtual provider

```bash
hermes -z "$(cat brief.md)" --provider moa -m <preset-name> -t ''
```

`-z` takes a literal string; the **preset name** goes in `-m` (not a model slug), and
the virtual provider is selected by `--provider moa`. In `-z` mode approvals are
auto-bypassed already, so `-t ''` is not about approval hangs — it loads no toolsets at
all, which is what you want for a pure text-in/text-out panel call. This is the path to
use from a script, a scheduled job, or when you want the panel result as plain text.

`-z` accepts a literal string argument only — there is no stdin flag — so a very large
brief goes on the command line via `"$(cat brief.md)"` and can hit the OS `ARG_MAX`
limit. If the brief is large enough to risk that, keep the argv prompt short and have
the panel read the detail from context you have already summarized into it, rather than
pasting an entire corpus.

### 3. Session switch — pick the preset in the model picker

Switches the whole session onto the panel until you switch back. Rarely what you want
for a single hard problem; it multiplies cost on every subsequent turn.

### Verifying a panel actually ran

**Never claim "N models answered" without evidence.** The aggregator produces fluent
output whether or not any reference succeeded — a failed slot degrades silently into a
single-model answer that reads exactly like a panel result.

Turn on tracing and read the trace:

```yaml
moa:
  save_traces: true # writes <hermes_home>/moa-traces/<session_id>.jsonl
```

Each trace line carries `references[]` with per-slot `label`, `provider`, `model`,
`output`, `usage`, and cost fields. **There is no `error` key** — a failed slot records
its failure _inside_ `output` as `[failed: ...]`, and a skipped one as `[skipped: ...]`.
So the check is: every configured reference must have `output` that is non-empty AND
does not start with `[failed:` / `[skipped:`. Read against the trace schema of your
installed Hermes version. If fewer families answered than your panel intended, stamp the
result `degraded: <n>-family` and say so in your report.

Leave `save_traces` off for routine work (it writes the full prompt and every reference
response to disk) and turn it on for runs whose provenance you need to defend.

## Configuring the panel

The panel lives in `moa:` in the profile config. Presets are named; `default_preset`
picks the one `/moa` uses.

```yaml
moa:
  default_preset: highstakes
  save_traces: false
  presets:
    highstakes:
      enabled: true
      fanout: user_turn # or per_iteration
      max_tokens: 4096
      reference_max_tokens: 1200
      reference_models:
        - { provider: <provider-alias>, model: <model> }
        - { provider: <provider-alias>, model: <model> }
        - { provider: <provider-alias>, model: <model> }
      aggregator: { provider: <provider-alias>, model: <model> }
```

Rules that the runtime enforces, and that you should design around:

- **A slot is `{provider, model}` plus an optional `reasoning_effort`.** There is **no
  per-slot system prompt, mandate, temperature, or role field.** Every reference gets
  the same fixed advisory system prompt and the same conversation view. Role
  differentiation must go in the BRIEF, not the config (see below).
- **`provider` must be a provider alias the local profile actually defines**, and the
  alias must match the model's API shape. On some providers a mismatch is not an error:
  the call returns HTTP 200 while being silently translated. Read the local config, pair
  each model with the provider block that genuinely fronts it, and confirm with a live
  call; never "normalize" slots onto one provider for tidiness.
- **`provider: moa` is rejected in a slot** — presets cannot recursively nest.
- **A slot missing `provider` or `model` is silently dropped** at read time and the
  preset can fall back to hardcoded defaults. After editing `moa:`, re-read the
  effective config and confirm your slots survived.
- Choose **different model families** for the references. Three slots on three builds of
  the same family is not a panel; it is one opinion with error bars.
- Do not seat the aggregator's own family in a reference slot when you can avoid it — it
  biases synthesis toward its own draft.

Resolve model names from the **live** local config or provider listing. Hardcoded slugs
go stale and a stale slug is worst exactly when you need the panel most.

**A renamed model identifier is a silent, total outage for every caller pinned to the
old name.** Providers typically return `404 model_not_found` at request time with no
fallback, and nothing warns you at edit time. When an identifier changes, the rename is
not done until you have swept **every** surface that names it — `moa:` slots, the
provider `models:` map, scheduled-job model pins, prompts, and docs — everywhere it is
configured. Verify by running the preset through the real config path, not just a raw
API call.

## Role differentiation without per-slot prompts

The old approach gave each seat a private mandate. The native runtime cannot do that:
every reference sees the same messages. So put the role structure **in the brief
itself** and ask each reference to answer all of it:

```text
Answer the problem below completely. Then, in clearly labelled sections, also give:
(a) FORMAL — the assumptions, invariants, and calculations that must hold, and what
    would make this answer invalid;
(b) RED TEAM — the sharpest attack on your own answer and the cheapest test that
    would kill it;
(c) CONTRARIAN — the strongest non-obvious alternative you rejected, and why.
```

Every reference produces a complete solution plus the three lenses; the diversity comes
from the model families genuinely differing, not from artificially narrowing each seat.

This is a real tradeoff, not a pure win. Per-seat mandates let each seat spend its whole
token budget on one lens, whereas here every seat covers everything within the shared
`reference_max_tokens` cap, so each lens gets less depth per seat. The compensating
advantage is that no seat can skip solving the problem and return only a fragment. If
you need real depth on one lens, raise `reference_max_tokens` or run a second, narrower
panel on that lens alone.

**No reference can use tools.** The runtime tells every reference explicitly that it
cannot call tools, run commands, browse, or read files, and instructs it to reason from
the context given. So a panel never performs live retrieval — not even a model whose
provider has live search. If the problem depends on current facts, **gather the evidence
first with your own tools and put it in the brief.**

## Orchestration method

1. **Gate.** Apply the routing rule above. If it fails the gate, route to a single model
   and say why. Most problems should exit here.
2. **Preserve the raw problem, then sharpen it.** Keep the user's exact words; write a
   brief that states the goal, hard constraints, non-goals, and what "good" means.
   Define the success criteria BEFORE you see any answers — otherwise you will grade
   toward whichever answer you happen to like.
3. **Gather grounding evidence.** Anything time-sensitive, private, or file-based must
   be collected by you and embedded in the brief. References are tool-less.
4. **Pick the seats from evidence, not habit.** Run
   `scripts/fitlog.py report --kind <task-class>` and let the recorded per-model/role
   history shape the preset you choose (or the preset you edit for this run). If the KB
   has too few samples for this task class, say so and pick on reasoned diversity
   instead — then step 9 makes the next run better informed.
5. **Convene the panel** via one of the three paths above.
6. **Verify the panel ran** via the trace. Stamp degradation honestly.
7. **Score each reference** against your pre-declared criteria before synthesizing.
8. **Build the component ledger** (`templates/synthesis-ledger.md`): pick ONE spine
   answer, then walk every distinct contribution from the others and give each an
   explicit disposition — grafted / rejected / already covered — with attribution. Graft
   only through an interface check: does the receiving design actually provide the state
   this component assumes?
9. **Adversarially test the synthesis.** Attack your own merged answer. If the merged
   answer is not demonstrably better than the best single reference, **ship the best
   single reference** and say so. Anti-mush is a real outcome, not a failure.
10. **Log the outcome** to `scripts/fitlog.py` — which model contributed what, at which
    role, on which task class, scored on the rubric dimensions the CLI accepts. This
    closes the loop that step 4 reads from: it is the only part of v1's tooling that
    survives, and it is what makes the NEXT panel's composition an evidence-based choice
    instead of a guess. Require several samples before overweighting a seat, and decay
    old scores.
11. **Report** with the panel composition, degradation stamps, the ledger, and the open
    risks.

## Scoring rubric

| Dimension          | 1                 | 3                         | 5                                       |
| ------------------ | ----------------- | ------------------------- | --------------------------------------- |
| **Soundness**      | wrong / unfounded | mostly right, gaps        | verifiably right, assumptions stated    |
| **Completeness**   | fragment          | covers the main path      | covers main + edges + failure modes     |
| **Actionability**  | vague direction   | needs real work to use    | an operator can execute without rework  |
| **Usable-Novelty** | generic rehash    | 1 usable non-obvious idea | multiple usable new levers (not sci-fi) |
| **Testability**    | no way to verify  | partial checks            | clear acceptance tests / numbers / code |

Hard gates first: anything that violates a mandatory constraint or is unsafe is
disqualified regardless of score.

## Why there is no runner script

Version 1 of this skill shipped `scripts/panel.py`, a hand-rolled `urllib` client with
its own `.env` parser, its own model-family resolver, its own hardcoded fallback pins,
and its own threadpool. It was written as if Hermes had no MoA support. It did.

That duplication was not free:

- It bypassed the profile's provider configuration, so it could not inherit the
  deployment's routing, authorization, or credential behavior — and could route a seat
  somewhere the profile never authorized.
- Its hardcoded model pins went stale silently and were consulted exactly when live
  resolution had already failed.
- Its fuzzy model-name matcher would fall back to a default family rather than fail, so
  a typo produced a confident answer from the wrong model.
- It produced no usage or cost accounting, and none of its calls appeared in the
  runtime's normal telemetry.

The native runtime handles all of that. **Do not reintroduce a bespoke fan-out script.**
If the native runtime is missing a capability you need, the correct move is to file it
upstream, not to route around the config layer.

`scripts/fitlog.py` remains: it is a local SQLite record of model-task fit that has no
runtime equivalent, and it makes no network calls.

## Pitfalls

1. **Assuming a fluent answer means the panel ran.** A dead reference slot is invisible
   in the output. Check the trace, not the prose.
2. **Expecting live retrieval.** Every reference is explicitly tool-less. Ground the
   brief yourself.
3. **Pairing a model with the wrong provider alias.** Provider aliases must match the
   configured provider and API shape for the target model. On some providers a mismatch
   does not error — it returns HTTP 200 and silently translates — so verify with a live
   call on your own deployment rather than assuming a mismatch fails loudly. Some
   deployments also front the _same_ model under more than one alias with different
   routing or billing behavior; if that is true where you run, confirm which path a slot
   actually took from the response metadata, not from the model string.
4. **Pinning a slot to a renamed model identifier.** The old name typically 404s at
   request time with no fallback; the panel loses that family entirely and the
   aggregator papers over it.
5. **Silently-dropped slots.** An incomplete slot is discarded at read time and the
   preset may revert to defaults. Re-read the effective config after editing.
6. **Treating a silently-truncated reference as a real proposal.** A slot can complete
   with no error and still return only the opening of an answer (observed in practice: a
   ~1KB fragment while sibling seats returned 5-27KB on the same brief). Length
   disparity is a WARNING, not proof — families differ in verbosity and a short answer
   may be complete and concise. Use it as a prompt to check whether the response is
   _structurally_ incomplete (stops mid-section, never reaches a conclusion, no coverage
   of the brief's asks) or whether a truncation/termination signal is available in the
   trace. Only then discount it as a partial; a genuinely concise answer can be the best
   one in the panel.
7. **Assuming a slow reference is a dead one.** Reasoning-heavy models can take many
   minutes while siblings finish in one or two, and the slowest seat is sometimes the
   sharpest. Wait for the run to actually finish before declaring a family missing.
8. **Reading a long reference linearly.** A very long response can be mostly visible
   chain-of-thought that never commits to an answer. Skim the structure and the final
   section first, harvest components into the ledger, and do not pull the whole thing
   into context.
9. **Correlated hallucination masquerading as consensus.** The models share training
   blind spots; if 4 agree, that raises confidence ONLY if their error sources are
   plausibly independent. Never report "N models agreed" as if agreement-count were
   quality. Validate against primary sources.
10. **Seating the aggregator's own family as a reference.** It biases synthesis toward
    its own draft.
11. **Leaving `save_traces` on permanently.** It writes full prompts and every reference
    response to disk. Enable it for runs you need to defend, then turn it off.
12. **Switching the session onto a preset and forgetting.** Every later turn pays panel
    cost. Prefer the one-shot paths.
13. **Panel-ing a problem that had a right answer.** If the question is verifiable,
    verify it — a panel on a factual question produces expensive agreement.
14. **Shipping the merge because you built it.** If the synthesis is not better than the
    best single reference, ship the single reference.

## Verification checklist

- [ ] Passed the GATE (or honestly routed to a single model instead)
- [ ] Raw problem preserved; sharpened brief written; "good" defined before answers seen
- [ ] Time-sensitive grounding gathered by the caller and embedded in the brief
- [ ] Panel ran via native `/moa` / `--provider moa` (no bespoke fan-out script)
- [ ] Every configured reference slot actually completed with usable content (trace
      checked); any shortfall stamped `degraded: <n>-family`
- [ ] Enough model FAMILIES answered to justify the claim being made about the result
- [ ] Each reference scored against pre-declared criteria
- [ ] Seats chosen from `fitlog.py report` history where samples exist (else noted)
- [ ] Outcome logged to `fitlog.py` so the next panel picks seats on evidence
- [ ] Component ledger built; ONE spine chosen; contributions have dispositions +
      attribution
- [ ] Synthesis adversarially tested; anti-mush fallback checked (ship best-single if
      not better)
