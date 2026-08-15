---
name: multi-review
description: >
  Use when reviewing almost any meaningful artifact, decision, action, plan, code
  change, prompt, skill, research summary, outbound message, or public-facing content.
  Runs a small panel of diverse review lenses across model families when available,
  synthesizes findings into fix/ask/defer/wontfix decisions, and iterates until the
  result is ready.
version: 1.2.0
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

**Count a reviewer only when it returned substantive review output.** A seat that merely
started, printed setup or shell noise, hung, or was killed is a _failed_ seat, not an
independent lens. A same-family external reviewer does not by itself clear a
`single-model-family` degradation. Report completed seats and distinct model-family
coverage separately — they are different numbers and conflating them overstates the
review.

**Do not stall silently.** Give each reviewer a deadline. Once the expected window
passes, poll for partial output rather than waiting indefinitely. If a seat times out or
returns empty, replace it when the selected depth still requires that coverage;
otherwise synthesize only if the completed seats still meet the depth floor for this
target. A degraded panel that drops below its floor is not a finished review — say so
instead of shipping it. Report which seats completed, which failed, and which families
are missing, and lower confidence to match what actually survived.

### Execution hierarchy

Use the strongest practical isolation mechanism available, but match it to the task's
shape:

1. **Native subagents with per-task model override** when the runtime supports selecting
   provider/model per child agent **and the reviewer task is bounded reasoning**: a
   self-contained artifact, a clear lens, and no exploratory I/O. This is best because
   prompts, context, and failures are naturally isolated.
2. **Headless Hermes one-shots** (`hermes -z ... --provider ... -m ... -t ''`) for pure
   text-in/text-out reviewer calls, especially when subagents cannot select model
   families. This is the most portable Hermes pattern. Use a higher timeout than the
   default for real reviews; 300-600 seconds is usually reasonable, and deep/slow model
   panels may need the upper end.
3. **Parent-gathered I/O + reviewer one-shots** for open-ended or I/O-heavy review work
   (large filesystem searches, email/search crawls, binary downloads, multi-step data
   collection). Do the I/O in the parent with normal tools, reduce it to a bounded
   brief, then send that brief to reviewers. Do **not** hand an open-ended crawl to
   subagents — many runtimes enforce a child timeout (often about 600 seconds), and the
   review will fail before synthesis.
4. **Same-model subagents with different lenses** when only one model family is
   available. Increase lens diversity, include at least one contrarian reviewer and one
   meta-review, and stamp `degraded: model-diversity unavailable`.
5. **Manual single-pass review** only for quick/low-stakes work that is **below every
   minimum depth floor** (see below). Never use this mode for money/auth/secrets/user-
   data/irreversible/public/rollout targets, even if they look small. Stamp
   `degraded: single-reviewer` and do not present it as a panel.

**Privacy applies to every path.** The re-injection rule in execution rule 3 below is
not specific to `--ignore-rules`: any isolated reviewer — native subagent, headless
one-shot, or same-model subagent — runs without the calling context's project rules. If
the artifact may contain private data, or you are in a repo with a privacy/PII policy,
copy those constraints into **every** reviewer prompt regardless of execution path.

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

Use the models configured in the local Hermes profile. Do not hard-code API keys or
provider aliases. Enumerate the live profile's `providers` mapping for every panel, then
choose an alias/model pair that is present now; aliases may be renamed or removed.

Pair each alias with the provider block the local config actually wires it to, and do
not "normalize" a reviewer onto a different provider for tidiness. When one router is
exposed through several provider blocks, they usually differ by API shape
(`chat_completions` vs `anthropic_messages`), and the better block depends on the
upstream model family: Claude models are natively Anthropic-shaped, most others (OpenAI,
xAI, Google) are natively OpenAI-shaped. The wrong block still returns HTTP 200, so this
fails silently rather than loudly. If an existing config contradicts that, assume it is
deliberate until you have checked why.

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
  High variance is useful for surfacing issues, not for final wording. Grok is also the
  family most likely to offer **native live X-graph retrieval** (via xAI's `x_search`
  server-side tool) rather than general web search, which helps when the question is
  "how are people reacting right now?" — public-facing copy, launch posts, naming,
  positioning, and reputational blast radius. Live retrieval applies only when the
  selected route actually enables that tool; otherwise treat Grok as cutoff-bound like
  any other model.
- **Local or small models** — useful for cheap/private quick passes, syntax/style
  checks, and obvious inconsistencies. Do not rely on them alone for high-stakes
  judgment.

These family strengths are **observed heuristics, not guarantees** — they shift with
model versions and prompting. Verify against the artifact in front of you rather than
treating them as fixed properties.

### When Grok is the right pick

Grok earns a seat (or the lead reviewer slot) when the artifact needs adversarial or
socially-grounded judgment rather than careful synthesis:

- **Critical / red-team review** — "tear this apart", pre-mortems, threat modeling,
  "what's the strongest argument this is wrong?"
- **Public reaction and sentiment** — how a post, product name, price change, or policy
  will land publicly; what critics will seize on. Treat this as _platform_ reaction (X
  discourse skews fast, vocal, and manipulable), not a proxy for the general public.
- **Current events grounding** — claims that depend on what happened recently, where a
  stale training cutoff produces confidently wrong review notes.
- **Contrarian check on consensus** — when the other reviewers agree suspiciously fast
  and you want a dissent probe.

Do **not** default to Grok for final wording, empathetic messaging, or careful policy
synthesis; its variance is a feature for finding problems and a liability for phrasing
them. Pair it with a lower-variance synthesizer from a different configured family, and
verify its claims rather than rubber-stamping them.

### Running reviewers as Hermes one-shots

The cleanest way to run an independent reviewer is a headless `hermes -z` call against a
chosen provider/model. Enumerate the live profile's `providers` mapping before using an
alias; do not copy aliases from an older panel or another profile.

**Keep reviewers on the configured router path — a slow reviewer is not a broken one.**
A `hermes -z` reviewer call pays chat-session startup (config load, memory/Cortex
prefetch, skill scan, system-prompt build) on top of the model's own latency, so a
single reviewer can take a minute or more even when everything is working. That slowness
is **not** a model failure and is **not** a reason to drop to a same-model panel or to
"optimize" by POSTing a router/provider endpoint directly. The fix for slowness is a
generous timeout plus parallelism _inside_ the configured Hermes/provider/router path
(including a custom OpenAI-compatible router) — never a bypass. The only exceptions are
the router being genuinely unreachable (for a diagnostic) or the human explicitly
approving a different architecture. For the concrete slow-vs-broken correction that
produced this rule, see `references/slow-reviewer-timeouts-router-path.md`. **Six
execution rules that prevent silent failures:**

1. **Mind the artifact size.** A `hermes -z "$PROMPT"` call places the whole prompt on
   the process argv, and command substitution like `hermes -z "$(cat file)"` does the
   same — it does **not** dodge the limit. Normal artifacts (a function, a small diff, a
   message) are fine. For large inputs — big PR diffs, full logs, multi-file dumps —
   argv can hit `ARG_MAX` (`Argument list too long`). When the artifact is large, prefer
   **chunking** into per-file/per-section reviews or gather in the parent and send a
   bounded brief. Do not pretend a temp file plus `$(cat ...)` solves this; it doesn't.
2. **Always disable tools with `-t ''`.** A headless reviewer that tries to call a tool
   will hang waiting for an approval that never comes. `-t ''` keeps it a pure text-in /
   text-out review. Do not remove it when customizing.
3. **Use a higher timeout than the default for real reviews.** Review models often take
   longer than normal chat, especially with long prompts or slow/deep models. When
   launching via an agent terminal tool, set the tool timeout to **300 seconds for
   normal reviews** and **600 seconds for deep/slow panels**. If the run still times
   out, first try to recover the coverage — shrink the artifact, split the panel, or
   replace the seat — rather than accepting the loss. Only when that fails does the
   degradation rule in the Core Contract apply: synthesize solely if the completed seats
   still meet this target's depth floor, and label the gap. Never silently fall back to
   a partial review.
4. **Use `--ignore-rules` deliberately, and re-inject any safety rules you still need.**
   It stops the calling profile's persona from washing out the review lens — but it also
   strips project rules. If the artifact may contain private data (real names, host
   paths, ports, secrets, internal context) or you're operating in a repo with a
   privacy/PII policy (for example an `AGENTS.md` zero-PII block), **copy those
   constraints into the reviewer prompt** so the headless reviewer doesn't echo
   sensitive data into its output or any follow-up text. Independence of lens, not loss
   of safety.
5. **Confirm the provider exists first** by enumerating the live profile's `providers`
   mapping before selecting it. Aliases are profile-local and can be renamed or removed;
   use the tested recipe under **Panel infrastructure traps**.

6. **Run a cross-family panel in parallel background processes — but never with shell
   `&`.** For a genuine multi-family panel, wall time should be the _slowest single
   reviewer_, not the sum, so run the reviewers concurrently. The Hermes terminal tool
   **rejects** foreground commands containing `&` backgrounding ("Use
   terminal(background=true)...") and also rejects `workdir` strings containing shell
   metacharacters, so a one-shot `cmd & cmd & wait` panel will be blocked. The working
   parallel pattern is: launch each reviewer with
   `terminal(background=true, notify_on_complete=true)` writing to a distinct
   `/tmp/out_<lens>.txt`, then read the files after all complete. **Default for a
   cross-family (2+ model) panel: parallel background** — the whole point of a panel is
   diversity, and parallelism is what makes waiting for slow-but-healthy reviewers
   affordable. Fall back to sequential foreground `hermes -z` calls only for a
   single-reviewer check or when background orchestration isn't available. **Do not
   stamp `degraded: single-model` just because a reviewer is slow** — only after a
   genuine failure or a blown timeout for the configured depth (300s for normal, 600s
   for deep). Early-degrade on slowness is the classic bug this rule exists to prevent.
   Confirmed in practice; parallel default reaffirmed after a later recurrence.

```bash
# Provider/model names are PLACEHOLDERS — resolve them from the local config.
# Suitable for normal-sized artifacts; for large inputs, chunk or send a bounded brief.
# When running these through an agent terminal tool, set timeout=300 for normal reviews
# and timeout=600 for deep/slow model panels.
hermes -z "$PROMPT_GROK"   --provider <grok-provider>   -m <current-grok-model>   --ignore-rules -t ''
hermes -z "$PROMPT_GEMINI" --provider <gemini-provider> -m <current-gemini-model> --ignore-rules -t ''
hermes -z "$PROMPT_GPT"    --provider <gpt-provider>    -m <current-gpt-model>    --ignore-rules -t ''
```

Do not copy a version number out of this document. Resolve each family's **current**
flagship from the local config or provider listing — hard-coded slugs go stale and
silently 404.

If the profile exposes a router combo/alias for a family, prefer the **combo name** over
a raw slug. A subscription-backed combo (for example an OAuth seat with a pay-per-token
fallback) prefers subscription quota, but **can still fall back to metered usage** — so
verify routing and cost policy rather than assuming a review is free.

If the profile routes everything through a custom multi-provider router, the providers
will instead be custom aliases. Enumerate the live config and use whatever families are
actually wired up — the skill cares about _family diversity_, not the exact alias.

### Panel infrastructure traps

Before launching reviewers, validate the plumbing in this order:

1. Resolve the reviewer executable from the running gateway's loaded `site-packages`,
   not a remembered path, `which` result, or version string alone. **This step applies
   when a gateway is running.** On a TUI or standalone CLI install with no gateway
   process, there is no PID to derive `site-packages` from: fall back to the interpreter
   that sits beside the resolved `hermes` binary, confirm it imports the Hermes package,
   and note in the run that provenance was resolved without a gateway.
2. Enumerate the active profile's provider aliases/models; never hard-code a custom
   alias across panel runs. Smoke-test the exact alias/model pair and require exit `0`
   plus non-empty output.
3. Bound every background reviewer poll with both a wall-clock deadline and a maximum
   iteration count. Break explicitly on completion and timeout; surface non-zero exit
   status and stderr.

The command-first, executed recipes and observed symptoms are in
[references/panel-infrastructure-traps.md](references/panel-infrastructure-traps.md).
For slow-but-valid reviewers, router-path and 300/600-second degradation policy remains
in
[references/slow-reviewer-timeouts-router-path.md](references/slow-reviewer-timeouts-router-path.md).

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

[If the artifact may contain private data or you are in a repo with a privacy/PII
policy, paste those constraints here so this reviewer keeps them in force.]

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
- Do not echo private data (names, host paths, ports, secrets) into your output.
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

### When reviewers disagree, reproduce — do not arbitrate

Two reviewers contradicting each other on a factual point is not a tie to be broken by
seniority, model reputation, or vote count. Go back to the artifact and check. The
cheapest decisive probe beats the most confident reviewer, and a reviewer asserting that
a real citation is fabricated is itself a common review error — verify before deleting
anything on a reviewer's say-so. Only genuine preference forks, where both positions
survive contact with the evidence, go to the user as a choice.

### Quantified claims need an evidence matrix

When a finding claims impact, separate four things that are easy to blur together:

| Question               | What it establishes                                  |
| ---------------------- | ---------------------------------------------------- |
| **Existence**          | the problem is real, with a located instance         |
| **Observed frequency** | how often it actually occurred, measured not guessed |
| **Measured impact**    | what it cost where it occurred                       |
| **Addressable impact** | what the proposed fix would actually recover         |

Existence does not imply frequency, and measured impact does not imply the fix recovers
it. Do not stack several estimates that all draw from the same underlying pool and then
report the sum — that double-counts. If only existence is established, say so and label
the rest as unquantified rather than attaching a number that will be quoted later as if
it were measured.

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

Two distinct ideas — keep them separate:

- **Stop criteria** decide when you may _stop reviewing_.
- **Verdict** reports the _readiness of the artifact_ given what is still open.

A finding is **actioned** (you may stop reviewing it) when it is one of: `fixed` ·
`asked` · `deferred-with-reason` · `wontfixed-with-reasoning` ·
`reported-with-recommendation`. **Stop criteria:** every Critical, High, and Medium
finding must be actioned before you stop; Lows do not block. Don't keep reviewing for
theoretical improvements forever. Actioned ≠ resolved — a deferred Critical is actioned
but still open.

A finding is **cleared** only when it is `fixed` or `wontfixed-with-reasoning`. Anything
still requiring work or a human decision (`asked`, `deferred`, `reported`, `held`) is
**open**, regardless of severity.

The verdict is then mechanical:

- **pass** — no open Critical, High, or Medium findings; every one was _cleared_ (fixed
  or wontfixed). Only un-actioned Lows may remain. Ready for the next step. A deferred
  or merely-reported Critical/High/Medium can **never** be `pass`.
- **edit** — open findings remain, but they are straightforward and applyable without a
  human judgment call.
- **hold** — an open finding needs a human decision before proceeding.
- **block** — unsafe, incorrect, or policy-violating to proceed in current form. Any
  open Critical that was not cleared lands here (or `hold`), never `pass`.

If the user asked you to **execute** and the fixes are safe, clear what you're
authorized to clear before reporting; don't stop with fixable open Critical/High/Medium.
If the task is **review-only**, you don't edit the artifact — report each open
Critical/High/Medium with a recommendation under `edit`/`hold`/`block`. That satisfies
the stop criteria (actioned) without claiming the artifact is ready (it isn't `pass`).

## Meta-Review

Before declaring success, review the review:

- Did the panel match the actual stakes, or did it over-focus on one domain?
- Did any reviewer hallucinate commands, requirements, or facts?
- Were false positives rejected with reasons rather than blindly fixed?
- Were all high-confidence findings actioned — fixed, asked, deferred-with-reason,
  wontfixed-with-reasoning, or reported-with-recommendation (and Critical/High only
  marked `pass` if actually cleared, not merely deferred)?
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
7. **Ignoring panel plumbing.** A wrong executable, a stale provider alias, or an
   unbounded watcher can imitate model failure. Run the binary-provenance, alias-smoke,
   and bounded-poll checks under **Panel infrastructure traps** before degrading the
   review.
8. **Ignoring missing setup.** If the environment lacks Grok/Gemini/GPT routing, fall
   back honestly and note how to improve the panel next time.
9. **Confusing optimization with defect discovery.** The goal is meaningful risk
   reduction, not generating feedback for its own sake.
10. **Letting `--ignore-rules` strip safety, not just persona.** It also removes project
    privacy/PII rules. Re-inject any privacy or safety constraints the artifact needs
    into each reviewer prompt before ignoring rules.
11. **Delegating open-ended I/O-heavy review work.** Subagents are best for bounded
    reasoning. For crawls, searches, downloads, or broad file inspection, gather and
    reduce in the parent first, then send reviewers a self-contained brief.
12. **Committing private routing notes to a public skill.** Profile-specific provider
    aliases, private endpoints, key env names, and owner-specific panel recipes belong
    in private profile references, not the public repo. Public guidance should describe
    the pattern and safety constraints, not private infrastructure.

## Verification Checklist

- [ ] Target, audience, and stakes identified
- [ ] Depth chosen based on risk
- [ ] Lenses selected for the scenario, not from habit
- [ ] At least two model families used when available (or degradation stamped if not)
- [ ] Privacy/PII constraints re-injected into every isolated reviewer prompt
- [ ] Reviewers ran independently
- [ ] Reviewer binary provenance matches the running gateway
- [ ] Provider aliases/models were enumerated from the live profile and smoke-tested
- [ ] Background reviewer polling has a deadline, iteration bound, and verified timeout
- [ ] Findings synthesized into auto-fix / ask / defer / wontfix
- [ ] Safe auto-fixes applied when authorized
- [ ] Material fixes re-reviewed
- [ ] Meta-review completed
- [ ] Final report labels model/panel degradation honestly
