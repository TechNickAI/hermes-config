---
name: multi-review
description: >
  Use when reviewing almost any meaningful artifact, decision, action, plan, code
  change, prompt, skill, research summary, outbound message, or public-facing content.
  Runs a small panel of diverse review lenses across model families when available,
  synthesizes findings into fix/ask/defer/wontfix decisions, and iterates until the
  result is ready.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [review, quality, multi-model, synthesis, safety]
    related_skills: [pr-review-sweep]
---

# Multi-Review

## Overview

Multi-review is a generic review pattern: look at an artifact through several
independent lenses, preferably using more than one model family, then synthesize the
findings into concrete next actions.

This is **not only** an outbound-communication gate. Outbound comms are one important
case, but the same pattern works for code, skills, plans, prompts, migrations, research,
public posts, tool actions, config changes, incident reports, and decisions with real
blast radius.

The point is diversity without chaos:

- **Different lenses** catch different failure modes.
- **Different model families** catch different blind spots.
- **A synthesis pass** deduplicates, rejects false positives, and turns feedback into
  action.
- **A meta-review pass** checks whether the review itself was useful or noisy before the
  caller declares the artifact ready.

## When to Use

Use this skill when the user says:

- "review this", "sanity check this", "second pair of eyes", "is this ready?"
- "check this before I send/post/ship/merge/run it"
- "run a multi-review", "get Grok/Gemini/GPT on this", "use multiple reviewers"
- "look over this plan", "review this PR", "review this skill/prompt"
- "is this safe?", "what am I missing?", "red-team this"

Also use it proactively before high-stakes actions:

- Code or config changes that will be committed, deployed, or rolled across machines
- Public or customer-visible writing
- Messages sent as a human/operator
- Data migrations, deletes, permission changes, or credential-handling changes
- Skill, prompt, or agent-behavior changes that future agents will follow
- Research summaries where citations, uncertainty, or omitted evidence matter

Do **not** use for:

- Trivial private-chat responses
- Purely read-only information gathering
- Cases where the user explicitly says not to review or says "no further action"
- Emergency containment where pausing for review would increase harm; contain first,
  review the follow-up

## Core Contract

A good multi-review run does these things, in order:

1. **Define the target.** Identify the artifact or action being reviewed, the intended
   audience, and the stakes.
2. **Choose review depth.** Pick quick, balanced, or deep based on risk.
3. **Choose a diverse panel.** Select lenses and model families appropriate to the task.
4. **Run reviewers independently.** Keep reviewer prompts isolated so they do not anchor
   on each other's conclusions.
5. **Synthesize.** Deduplicate findings, classify each as fix / ask / defer / wontfix,
   and decide whether the artifact is ready.
6. **Act.** Apply obvious low-risk fixes when authorized; ask once for judgment calls;
   defer only when scope genuinely exceeds the task.
7. **Meta-review.** Check whether the review was useful, whether the panel missed an
   obvious lens, and whether false positives were handled correctly.
8. **Iterate until ready.** Re-run targeted reviewers after material fixes, especially
   for high-stakes or public-facing artifacts.

Never claim "multi-model review" unless multiple model families actually ran. If model
routing is unavailable, say `degraded: single-model` and explain what still ran.

### Execution hierarchy

Use the strongest practical isolation mechanism available:

1. **Native subagents with per-task model override** when the runtime supports selecting
   provider/model per child agent. This is best because prompts, context, and failures
   are naturally isolated.
2. **Headless Hermes one-shots** (`hermes -z ... --provider ... -m ... -t ''`) when
   subagents cannot select model families. This is the most portable Hermes pattern.
3. **Same-model subagents with different lenses** when only one model family is
   available. Increase lens diversity, include at least one contrarian reviewer and one
   meta-review, and stamp `degraded: model-diversity unavailable`.
4. **Manual single-pass review** only for quick/low-stakes work. Stamp
   `degraded: single-reviewer` and do not present it as a panel.

## Depth Scaling

**Quick** — 1-2 reviewers. Use for low-stakes drafts, small edits, or a simple sanity
check.

**Balanced** — 3 reviewers. Default for meaningful work. Cover the primary domain,
truth/correctness, and user/audience impact.

**Deep** — 5+ reviewers plus meta-review. Use for security-sensitive changes, public
posts, fleet/config rollouts, irreversible actions, architecture, migrations, or changes
that future agents will rely on.

If unsure, use balanced. Escalate to deep when any reviewer finds a high-severity issue
or when the artifact will be hard to undo after release.

### Minimum depth floors

Certain targets should never get only a quick pass:

- **Code that handles money, auth, permissions, secrets, user data, networking, or data
  migration** → balanced minimum; deep if public or production-bound.
- **Irreversible tool actions, deletes, permission changes, or fleet/config rollouts** →
  deep minimum plus explicit approval/rollback review.
- **Public-facing policy, docs, prompts, or skills that future agents will follow** →
  balanced minimum; deep if the instructions affect safety boundaries.
- **Messages sent as a human/operator, legal/medical/financial statements, or sensitive
  interpersonal comms** → balanced minimum with empathy, evidence, and data-exposure
  lenses.

## Model Family Selection

Prefer a reviewer from a **different model family** than the calling agent. Independence
matters more than raw benchmark rank.

Use the models configured in the local Hermes profile. Do not hard-code API keys. If the
profile has aliases such as `custom:grok`, `custom:gemini`, or `custom:openrouter`, use
those; otherwise inspect the local config and choose equivalent provider/model pairs.

### Family strengths

- **Claude / Anthropic** — best for synthesis, nuanced tradeoffs, voice, empathy, policy
  interpretation, and turning messy findings into a coherent final answer. Avoid using
  only Claude if the calling agent is already Claude-family.
- **GPT / OpenAI** — strong structured reviewer: code correctness, API contracts, tests,
  consistency, deterministic triage, and concise fix recommendations.
- **Gemini / Google** — strong long-context reader: large diffs, logs, docs, research,
  cross-file consistency, evidence extraction, and "did the artifact cover the whole
  source?" checks.
- **Grok / xAI** — strong contrarian/red-team reviewer: assumptions, edge cases, blunt
  risk, adversarial misuse, policy gaps, and "what would embarrass us if true?" checks.
  High variance is useful for surfacing issues, not for final wording.
- **Local or small models** — useful for cheap/private quick passes, syntax/style
  checks, and obvious inconsistencies. Do not rely on them alone for high-stakes
  judgment.

### Running reviewers as Hermes one-shots

The cleanest way to run an independent reviewer is a headless `hermes -z` call against a
chosen provider/model. Confirm the local profile actually has the provider before using
it: `hermes config get model.providers` (or read `~/.hermes/config.yaml`).

**Three execution rules that prevent silent failures:**

1. **Never inline a large artifact into the command string.** PR diffs, logs, and full
   files blow past the shell `ARG_MAX` limit (`Argument list too long`) and break on
   quote nesting. Write the full prompt (lens instructions + artifact) to a temp file,
   then read it in: `hermes -z "$(cat /tmp/review-grok.txt)" ...`. For very large or
   binary-ish inputs, invoke the subprocess from the `execute_code` Python tool instead
   of a shell.
2. **Always disable tools with `-t ''`.** A headless reviewer that tries to call a tool
   will hang waiting for an approval that never comes. `-t ''` keeps it a pure text-in /
   text-out review. Do not remove it when customizing.
3. **Use `--ignore-rules`** so the calling profile's persona and project rules do not
   wash out the specialized review lens.

```bash
# Provider/model names are illustrative — match them to the local config.
# Public/standard providers:
hermes -z "$(cat /tmp/review-grok.txt)"   --provider openrouter -m x-ai/grok-4.3            --ignore-rules -t ''
hermes -z "$(cat /tmp/review-gemini.txt)" --provider google     -m gemini-2.5-pro           --ignore-rules -t ''
hermes -z "$(cat /tmp/review-gpt.txt)"    --provider openai     -m gpt-4o                   --ignore-rules -t ''
```

If the profile routes everything through a custom multi-provider router, the providers
will instead be custom aliases (for example `custom:grok`, `custom:gemini`,
`custom:openrouter`) with router-qualified model IDs. Inspect the config and use
whatever families are actually wired up — the skill cares about _family diversity_, not
the exact alias.

## Lens Selection by Scenario

### Code or Pull Request

Default panel:

- Correctness / logic
- Security / data exposure
- Tests / regressions
- Error handling / reliability
- Architecture / maintainability for deep reviews
- Performance only when the code path is hot or data volume matters

Good model mix: GPT for structured code triage, Gemini for large diff/context reading,
Grok for adversarial/security assumptions, Claude for synthesis.

### Skill, Prompt, or Agent Behavior

Default panel:

- Trigger clarity: will future agents load it at the right time?
- Operational truth: are commands, paths, flags, and tool semantics real?
- Procedure quality: does it tell the agent what to do next, not just describe a
  concept?
- Safety/boundary handling: approvals, secrets, public/private data, irreversible
  actions
- Failure modes: what happens when a tool is unavailable, output is huge, or the model
  is wrong?
- Retrieval/readability: concise enough to load, structured enough to follow

Good model mix: Gemini for long-context/coverage, GPT for structure and command
specificity, Grok for adversarial prompt misuse, Claude for final synthesis and voice.

### Outbound Communication

Default panel:

- Empathy / recipient experience
- Intent fidelity: does it say what the operator meant?
- Evidence: are claims true and appropriately qualified?
- Data exposure: does it leak internal, private, or wrong-person information?
- Voice and audience fit

Good model mix: Claude for tone and empathy, GPT for concise edits, Gemini for evidence
checking, Grok for blunt risk on sensitive messages.

### Plan, Strategy, or Decision

Default panel:

- Assumptions and missing information
- Dependency/order-of-operations risk
- Blast radius and reversibility
- Concrete next actions and ownership
- Contrarian review: what would make this fail?

Good model mix: Grok for contrarian pressure, Gemini for context coverage, GPT for
structured plan critique, Claude for decision synthesis.

### Tool Action, Config Change, Migration, or Rollout

Default panel:

- Target correctness: right host/profile/file/channel/account?
- Blast radius and rollback path
- Approval gate: does this need human go-ahead?
- Verification: how will we know it worked?
- Data exposure / secrets
- Idempotency and partial-failure handling

Good model mix: GPT for step correctness, Grok for failure/edge cases, Gemini for large
logs/configs, Claude for operator-facing summary.

### Research, Writing, or Analysis

Default panel:

- Evidence and citations
- Omitted counterarguments
- Audience usefulness
- Overclaiming / uncertainty
- Narrative clarity

Good model mix: Gemini for source coverage, GPT for structure, Grok for skeptical
counterarguments, Claude for prose synthesis.

## Reviewer Prompt Pattern

Give each reviewer the same target but a different job. Keep prompts focused:

```text
You are the <lens> reviewer in a multi-review panel.

Review target:
<artifact or action plan>

Context:
<audience, stakes, constraints, what done means>

Your job:
- Look only through the <lens> lens.
- Find issues that matter in practice, not theoretical nits.
- For each finding, include severity, confidence, evidence/location, why it matters, and
  the smallest useful fix.
- Distinguish real issues from tradeoffs, false positives, and merely theoretical
  improvements.
- Do not rewrite the whole artifact unless the lens requires it.
```

Do not ask every reviewer to solve everything. Reviewers are sharper when their scope is
narrow.

## Synthesis Workflow

After reviewers finish, synthesize into action buckets. Require evidence: each material
finding should point to the artifact location, reviewer observation, or source fact that
supports it. Unsupported claims are hypotheses, not findings.

### Severity scale

- **Critical** — unsafe to proceed; likely security/data loss/financial harm,
  irreversible blast radius, or a direct contradiction of the task.
- **High** — material correctness, safety, trust, or operational risk; should be fixed
  or explicitly accepted before proceeding.
- **Medium** — real issue that can bite later or degrade quality; fix when low-cost, ask
  if tradeoffs exist.
- **Low** — polish, clarity, maintainability, or preference; do not block unless many
  lows combine into user-visible quality risk.

### Deduplication rule

Merge findings when they share the same root cause, same practical fix, or same
operational impact. Keep the highest severity/confidence from the merged set and note
which reviewers caught it. Do not count the same bug three times just because three
models saw it.

### Action buckets

**Auto-fix** — Apply immediately when authorized. High confidence, low risk, and no
product/human judgment needed.

Examples: typos, broken commands, missing verification step, obvious factual mismatch,
formatting, unused import, missing null check where a crash is certain.

**Ask** — Batch into one user question. Use when reasonable people might choose
different fixes, behavior changes, public voice changes, security posture, or scope
expansion.

**Defer** — Valid but outside current scope. Record clearly if follow-up tracking
exists; do not use defer to dodge a flaw that blocks the current task.

**Wontfix / false positive** — The reviewer missed context or the fix is worse than the
risk. Explain the reasoning briefly. Complexity is a real cost.

### Verdicts and stop criteria

- **pass** — no material findings remain; the artifact is ready for the intended next
  step.
- **edit** — changes are needed, but they are straightforward and can be applied without
  a human judgment call.
- **hold** — a human decision is needed before proceeding.
- **block** — unsafe, incorrect, or policy-violating to proceed in current form.

If the user asked you to execute and the fixes are safe, apply auto-fixes before
reporting. If the task is review-only, report without modifying. Stop when every High or
Critical finding is fixed, held for human decision, or explicitly accepted as wontfix
with reasoning; don't keep reviewing for theoretical improvements forever.

## Meta-Review

Before declaring success, review the review:

- Did the panel match the actual stakes, or did it over-focus on one domain?
- Did any reviewer hallucinate commands, requirements, or facts?
- Were false positives rejected with reasons rather than blindly fixed?
- Were all high-confidence findings either fixed, asked, or explicitly deferred?
- Is the final artifact simpler and safer, not just more complicated?
- Did the synthesis honestly label degraded conditions, such as unavailable models?

For deep reviews, run a final small prompt against one independent model:

```text
Meta-review this review result. Did the panel miss a necessary lens, accept false
positives, overcomplicate the artifact, or fail to turn findings into action? Return only
material improvements.
```

Iterate if the meta-review finds material gaps.

## Output Format

Return a concise review report:

```markdown
## Multi-review result

**Target:** <what was reviewed> **Depth:** quick | balanced | deep **Panel:**
<lens/model list; include degraded notes> **Verdict:** pass | edit | hold | block

**Auto-fixed**

- <issue> → <change made> (<reviewer/model>)

**Needs decision**

- <issue> → recommendation + tradeoff

**Deferred**

- <issue> → why out of scope / follow-up

**Wontfix / false positive**

- <issue> → why not applied

**Verification**

- <tests/checks/re-review performed>

**Meta-review**

- <what improved after reviewing the review, or "no material gaps found">
```

Omit empty sections. If the verdict is `edit` and you are not authorized to edit,
include the proposed patch or rewrite. If the verdict is `hold` or `block`, explain the
smallest path to unblock.

## Common Pitfalls

1. **Calling it multi-model when it was not.** If only one model family ran, stamp the
   run as degraded.
2. **Letting reviewers see each other first.** This creates anchoring. Run independent
   reviewers before synthesis.
3. **Over-fixing theoretical issues.** Good review reduces risk; it should not turn
   clear work into defensive sludge.
4. **Skipping meta-review.** The synthesis can be worse than the raw findings if it
   blindly accepts noise.
5. **Using the same panel for every task.** Code, comms, plans, and rollouts fail in
   different ways.
6. **Forgetting the approval gate.** A review can recommend an action, but irreversible
   changes, public sends, secrets, money, and broad rollouts still need human approval.
7. **Ignoring missing setup.** If the environment lacks Grok/Gemini/GPT routing, fall
   back honestly and note how to improve the panel next time.
8. **Confusing optimization with defect discovery.** The goal is meaningful risk
   reduction, not generating feedback for its own sake.

## Verification Checklist

- [ ] Target, audience, and stakes identified
- [ ] Depth chosen based on risk
- [ ] Lenses selected for the scenario, not from habit
- [ ] At least two model families used when available
- [ ] Reviewers ran independently
- [ ] Findings synthesized into auto-fix / ask / defer / wontfix
- [ ] Safe auto-fixes applied when authorized
- [ ] Material fixes re-reviewed
- [ ] Meta-review completed
- [ ] Final report labels model/panel degradation honestly
