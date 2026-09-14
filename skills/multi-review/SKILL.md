---
name: multi-review
description: "Use when reviewing any meaningful artifact or decision."
version: 1.7.0
license: MIT
compatibility: >
  Portable review method. Native multi-model orchestration examples are Hermes-specific;
  Claude can run the method with Claude subagents but loses model-family diversity.
metadata:
  hermes:
    tags: [review, quality, multi-model, synthesis, safety]
    related_skills: [pr-review-sweep]
---

# Multi-Review

> **Full trigger context (was the frontmatter description):** Use when reviewing almost
> any meaningful artifact, decision, action, plan, code change, prompt, skill, research
> summary, outbound message, or public-facing content. Runs a small panel of diverse
> review lenses across model families when available, synthesizes findings into
> fix/ask/defer/wontfix decisions, and iterates until the result is ready.

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
   Whenever the run has **two or more seats**, staff for both generation and
   verification — those are usually different seats, not one strong seat (see
   "Verification and originality are usually different seats"). A single-seat run cannot
   split the roles; it carries the generator/verifier tension inside one prompt and is
   stamped degraded accordingly.
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

1. **Headless Hermes one-shots in isolated scratch homes** (`hermes -z ... -t ''`, each
   with its own `HERMES_HOME`) — the default. This is the only path that delivers a
   **different prompt AND a different model per seat** ("Grok, be critical"; "Claude, be
   empathetic"), and **the only path where you can size the timeout to the job.**
   Isolation is mandatory, not optional — see "Isolate every headless reviewer" below.
   Use a higher timeout than the default for real reviews; 300-600 seconds is usually
   reasonable, and deep/slow model panels may need the upper end.

   **NEVER recursively copy an active Hermes profile into a reviewer home.** Do not use
   `cp -r ~/.hermes/profiles/<profile> ...`, `rsync` the profile, or clone `state.db`.
   The profile contains a multi-GB live database. On 2026-08-26, four concurrent
   hand-rolled reviewer seats copied the Kenbot profile, filled the 96 GB root
   filesystem to 100%, and caused SQLite `disk I/O error` while the gateway was
   recording a live cron execution. Use `scripts/reviewer_home.sh`: it copies only
   `config.yaml`, `.env`, and `auth.json` (about 26 KB) and gives every seat its own new
   database. If the helper cannot initialize, the panel does not run.

   See execution rule 3 — the default is very likely lower than you want.

2. **Native subagents** when every seat can run on the _same_ model and you only need
   lens diversity: prompts, context, and failures are naturally isolated. **Two
   tradeoffs before choosing this path.** First, the delegation tool has **no per-task
   model parameter**, and upstream has repeatedly declined to add one (PRs #17718,
   #23266, #25026, #34773, #36790; maintainer on #34773: _"We do not want this"_). Every
   child in a batch runs on the single configured delegation model, so this path cannot
   staff a multi-model panel — do not plan one around it. Second, a subagent's runtime
   almost certainly gives you no per-call timeout control: the schema exposes goal,
   context, role, and output schema — not a deadline. Any wall-clock cap is process-wide
   configuration read at call time, so a skill cannot scale it to the artifact. If this
   review needs a deadline proportional to its scope, use path 1 or 3.
3. **Parent-gathered I/O + reviewer one-shots** for open-ended or I/O-heavy review work
   (large filesystem searches, email/search crawls, binary downloads, multi-step data
   collection). Do the I/O in the parent with normal tools, reduce it to a bounded
   brief, then send that brief to reviewers. Do **not** hand an open-ended crawl to a
   subagent whose deadline you do not control: if a wall-clock cap is configured, the
   child is killed mid-task and its findings die with it, and if none is configured a
   wedged child stalls the panel instead. Either way the review fails before synthesis.
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

## Review the METHOD before the work, not only the result

A review that arrives after the work is done can only find defects in the output. It
cannot recover the effort already spent on the wrong approach — and a flawed method
produces a clean-looking artifact that is wrong in a way no output review will catch.

For any substantial piece of work — research, analysis, a build, a migration — run two
reviews at two different stages, and be explicit about which one you are running:

- **Method review, BEFORE you begin.** The target is the thesis, the approach, and how
  you intend to determine the answer: what would falsify it, what population and window,
  what instrument, what would make the result meaningless. This is the cheaper review by
  a wide margin, because its findings cost nothing to act on.
- **Result review, AFTER the work.** The target is the produced artifact and its claims,
  with the method already agreed so reviewers judge execution rather than re-litigating
  the approach.

Then iterate until it holds, and only scale after it does. When a batch is involved,
review the method, do a small number, review those results, fix, do a few more, and
expand only once the small batch survives — never run the full batch on an unreviewed
method.

A single result-only review on a large finished body of work is the degraded case. It is
better than nothing and it is not this pattern; say which one you ran.

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

Pair each alias with the provider block the local config actually wires it to, and do
not "normalize" a reviewer onto a different provider for tidiness. When one router is
exposed through several provider blocks, they usually differ by API shape
(`chat_completions` vs `anthropic_messages`), and the better block depends on the
upstream model family: Claude models are natively Anthropic-shaped, most others (OpenAI,
xAI, Google) are natively OpenAI-shaped. The wrong block still returns HTTP 200, so this
fails silently rather than loudly. If an existing config contradicts that, assume it is
deliberate until you have checked why.

### Family strengths

The notes below combine general observation with one **measured** data point: a
multi-model bake-off where eight models were given the same open-ended research and
ideation brief, and their outputs were scored across seven weighted dimensions (mandate
discipline, evidence quality, originality, verification, usability, and related axes).
Findings from that run are marked **[measured]**. Everything else is ordinary heuristic.

Read the scope honestly: that was **one task, scored once, N=1 per model**, and the task
was _research and ideation_, not _critique of an existing artifact_. Behaviors that are
themselves review-shaped — adversarial pressure-testing, mandate discipline,
verification effort — transfer to reviewing with reasonable confidence. Generative
traits like originality are a **hypothesis** about review behavior, not an established
one. Treat the whole section as a prior to check against the artifact in front of you,
not as fixed model properties.

- **Claude / Anthropic** — best for synthesis, nuanced tradeoffs, voice, empathy, policy
  interpretation, and turning messy findings into a coherent final answer.
  **[measured]** Also the strongest self-critic in the run: it pressure-tested its own
  output adversarially, which is review behavior directly. **Its limit is divergence** —
  it scored lowest of the frontier models on originality, and every idea it produced
  also appeared on another model's list. It is a convergence engine, not a divergence
  engine. A panel staffed only with Claude models will produce a well-written consensus
  and miss the objection nobody else thought of. Avoid using only Claude if the calling
  agent is already Claude-family.
- **GPT / OpenAI** — strong structured reviewer: code correctness, API contracts, tests,
  consistency, and concise fix recommendations. **[measured] Best at building the
  evaluation scaffold, weakest at then executing it.** It produced the best decision
  framework in the corpus — a crisp set of screening questions others should have been
  measured against — and then over-filtered its own candidates and delivered almost no
  verification behind them. Give GPT the job of defining the review criteria, or the job
  of applying them, but do not assume one seat does both well.
- **Gemini / Google** — strong long-context reader: large diffs, logs, docs, and
  cross-file consistency checks. **[measured] Do not rely on it for evidence
  extraction**, despite the long-context strength: its evidence was correct but generic
  and thin, list-shaped with no numbers behind the claims. **Operational warning:** in
  that run a Gemini seat silently fell back to a different underlying model mid-thread
  and kept answering as if nothing had changed. A reviewer that swaps model families
  without telling you breaks the independence guarantee the entire panel rests on.
  Verify which model actually answered before counting it as family coverage.
- **Grok / xAI** — strong contrarian/red-team reviewer: assumptions, edge cases, blunt
  risk, adversarial misuse, policy gaps, and "what would embarrass us if true?" checks.
  **[measured] The best mandate discipline in the run** — it was the one model willing
  to answer the question actually asked rather than the more flattering adjacent
  question, and it rejected the framing it had been handed when the framing was wrong.
  That is precisely what a red-team seat is for. **[measured] Its failure mode is
  delivery:** it scored worst in the run on usability, producing genuinely excellent
  analysis and then leaving nearly all of it in scratch files while its actual reply led
  with process commentary. Grok needs an explicit output path and a demand for the
  artifact more than any other family (see rule 7). High variance is useful for
  surfacing issues, not for final wording.

  Grok is also the family most likely to offer **native live X-graph retrieval** (via
  xAI's `x_search` server-side tool) rather than general web search, which helps when
  the question is "how are people reacting right now?" — public-facing copy, launch
  posts, naming, positioning, and reputational blast radius. Live retrieval applies only
  when the selected route actually enables that tool; otherwise treat Grok as
  cutoff-bound like any other model.

- **Open-weight models** (Kimi, Qwen, MiniMax, DeepSeek, Llama, and similar) — **the
  divergence seats.** **[measured]** In that run the open-weight models produced the
  genuinely novel material: the mechanisms and framings that appeared on no frontier
  model's list. If a panel needs an idea the consensus will not generate — an unlisted
  failure mode, an approach nobody considered, a structurally different objection — this
  is where it comes from, and it is a real reason to seat one even when a frontier model
  is available. **[measured] They pair novelty with almost no verification:** the same
  seats that generated the new material asserted it without checking, and one of them
  also scored near the bottom on usability. Their output is a lead to be verified, never
  a finding to be trusted as-is. Cost and privacy (local or self-hosted execution) are
  secondary reasons to use them; capability diversity is the primary one.
- **Small or local models** — useful for cheap/private quick passes, syntax/style
  checks, and obvious inconsistencies. Do not rely on them alone for high-stakes
  judgment. This is a size/deployment distinction, not the open-weight distinction
  above: a large open-weight model run through a hosted router is a full-strength seat.

**Newer is not automatically stronger.** A vendor's latest flagship, including one
marketed as a reasoning improvement over the previous flagship, may not outperform the
model it supersedes on your actual work. Two Anthropic frontier models in that run
finished close together despite one being positioned as the clear successor. Check the
newer model on a task you have already scored before promoting it to a panel seat by
reputation.

These family strengths are **observed heuristics, not guarantees** — they shift with
model versions and prompting. Verify against the artifact in front of you rather than
treating them as fixed properties.

### Verification and originality are usually different seats

The single most useful structural finding from that run: **the models that verified
heavily generated almost nothing new, and the models that generated the novel material
verified almost none of it.** The correlation ran in opposite directions across the
whole field, frontier and open-weight alike.

Do not fight this by asking one seat to do both. Whenever the run has two or more seats,
staff for it:

- Seat at least one **generator** (open-weight models are the measured pick) whose job
  is to produce candidate findings, including speculative ones.
- Seat at least one **verifier** (frontier models, Claude and GPT in that run) whose job
  is to check the generator's claims against the artifact and kill the unsupported ones.
- Let the verifiers grade the generators. An unverified novel finding is a lead, not a
  result, and it should be labeled that way in synthesis until someone checks it.

A panel of only verifiers returns a tidy consensus that misses the unlisted problem. A
panel of only generators returns a pile of confident claims you cannot act on. The
review is the interaction between them.

**Single-seat runs are the documented exception.** A quick-depth check or the
`degraded: single-reviewer` fallback has one seat and cannot split these roles. Do not
try to fake a panel out of it. Instead, make the tension explicit inside the one prompt
— ask for candidate findings _and_ a verification pass over them, in that order — and
treat its novel-but-unchecked claims as leads, exactly as you would from a generator
seat. This is weaker than two seats, which is what the degradation stamp is telling the
reader.

### Demand the artifact, not the summary

**[measured]** In that run the single largest score gap was not analysis quality but
delivery: one model did roughly 94KB of excellent work, published about 1.2KB of it, and
opened its reply with commentary about its own tooling — leaving a reasonable reader to
conclude it had accomplished nothing. The work existed. It was simply never handed over.

This is the same failure class as rule 7 (incremental findings files), seen from the
other end, and it changes what you ask for:

- Give every reviewer an explicit output path and require the findings to land there.
- Judge a seat by the file it produced, not by the chat message it returned. A thin
  reply over a substantial file is a delivery failure, not a weak review — go read the
  file.
- Treat process commentary in a reviewer's response as a smell. A seat narrating its
  helper scripts is usually a seat that has not yet told you what it found.

### Retrieval is the weak link — paste the prior decisions in

**[measured]** In that run, none of the eight models consulted the existing record of
what had already been tried, and several confidently re-proposed approaches that had
already been evaluated and rejected, with the measured verdicts sitting in an accessible
store the whole time.

Assume a reviewer will not find your prior decisions on its own, even when it has the
tools and the access. A reviewer that re-raises a settled question burns a seat and adds
noise to synthesis. Paste the relevant history — the graveyard of rejected approaches,
the constraints already agreed, the decisions already made and why — directly into the
reviewer prompt as part of the bounded brief. Retrieval you did not verify is retrieval
that did not happen.

### Job-to-family quick reference

Measured on one research task, N=1 per model. A starting prior, not a routing table —
and never a substitute for the different-family independence rule above.

| Job                                             | Start with                                                     |
| ----------------------------------------------- | -------------------------------------------------------------- |
| Synthesize findings into a coherent verdict     | Claude                                                         |
| Write the evaluation framework or rubric        | GPT                                                            |
| Apply a rubric and verify claims                | Claude or GPT (not the one that wrote it)                      |
| Generate novel objections and unlisted failures | Open-weight (Kimi, Qwen, MiniMax, ...)                         |
| Adversarial kill / mandate discipline           | Grok                                                           |
| Read a large diff or corpus for coverage        | Gemini                                                         |
| Extract evidence with numbers behind it         | Not Gemini — Claude or GPT                                     |
| Anything where the deliverable itself matters   | Any seat — but give an explicit output path and check the file |

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

### Isolate every headless reviewer in its own scratch home (REQUIRED)

**Use the helper API exactly. Do not hand-roll this path.** Export the active profile as
`HERMES_HOME` when the shell did not inherit it, source the helper, and call
`reviewer_run`. `reviewer_home.sh` is a sourced function library, not an executable that
prints a home. Running `bash reviewer_home.sh ...` does nothing; a fallback that then
invents `/tmp/home-*` recreates an unseeded reviewer with no credentials.

`hermes -z` boots a full agent, and the CLI path opens the **calling profile's**
`state.db` read-write (`cli.py:4642` → `SessionDB()`, `cli.py:8566` →
`create_session(...)`, resolved by `hermes_state.py:2798`). Run that from an agent whose
gateway is live and you have **two OS processes writing one WAL database** — each with
its own lock state and its own view of the WAL index. No pragma prevents the damage.

This is not hypothetical. On a production host the gateway (fd mode `u`) and a
`hermes -z` reviewer (fd mode `u`) were caught holding one `state.db` simultaneously.
That 3GB database took structural B-tree damage — `invalid page number`,
`2nd reference to page`, rowids out of order — and had to be rebuilt offline from
readable rows.

**Give each reviewer its own scratch home.** Use the bundled helper
`scripts/reviewer_home.sh` (the larger panel runner is
`templates/parallel_reviewer_runner.sh`):

```bash
# Required when the shell did not inherit the active profile.
export HERMES_HOME=/path/to/the/active/profile
source "$SKILL_DIR/scripts/reviewer_home.sh"
reviewer_pool_init                    # REQUIRED; never inside $( )

# reviewer_run owns the exact CLI shape: hermes -z "$PROMPT" [options].
# The prompt must immediately follow -z because -z takes one positional value.
reviewer_run "$CRITICAL_PROMPT"   -m grok         &
reviewer_run "$EMPATHETIC_PROMPT" -m claude-think &
reviewer_run "$SECURITY_PROMPT"   -m gpt-5.6-sol  &
wait
reviewer_pool_destroy                 # or let the EXIT trap do it
```

**Never execute `reviewer_home.sh` with `bash` and never fall back to an unseeded
directory.** It only defines functions when sourced. If initialization or seeding fails,
the panel does not run.

`HERMES_HOME` roots config.yaml, `.env`, `auth.json`, skills, memories **and** state.db,
so a seeded scratch home gives working credentials plus a private database. Nothing is
registered under `profiles/`, so there is no namespace to garbage-collect and no name to
collide with — the scratch dies with the run.

**Reviewers are anonymous.** Run 2 or 10; the helper never names or enumerates personas.
The PROMPT decides what each seat is, so a panel can be whatever that day's artifact
needs. Never hardcode a role vocabulary into the tooling.

**One home per reviewer, never one shared home.** Measured: 6 concurrent reviewers
sharing a single home produced **6 simultaneous holders of one database** — the original
bug, relocated. Per-reviewer homes measure a peak of exactly **1**. Integrity surviving
one shared run proves nothing.

**Measured cost:** seeding a home ~1.2 ms / ~26 KB; state.db created on demand ~232 KB;
10 concurrent reviewers finished in 12.1 s using 2.7 MB of scratch, fully removed.
Creating a home is far cheaper than the model call it wraps — never batch reviewers into
one home to "save" it.

#### Five ways this helper can betray you (all measured, all guarded)

A reviewer panel found each of these in the first version of this helper. If you write
your own, handle all five — every one fails _silently_.

1. **Unchecked `mktemp` → empty `HERMES_HOME` → the caller's live database.** Hermes
   treats an empty `HERMES_HOME` as unset and falls back to `~/.hermes/state.db`. The
   isolation helper then causes exactly the corruption it exists to prevent. Validate
   every scratch path and **refuse to run** without one.
2. **Lazy auto-init inside `$(reviewer_home)` self-destructs.** Command substitution
   runs in a subshell whose EXIT trap fires when the substitution closes, deleting the
   pool and handing the reviewer an _unseeded_ home — the 401-with-rc=0 path. `$$`
   cannot detect a subshell (bash keeps the parent's pid) and `BASHPID` is empty on bash
   3.2 (macOS). Require explicit init.
3. **A signal handler that does not exit lets the script resume.** Bash returns control
   to the next statement, so a Ctrl-C'd fan-out destroys the pool and then seeds a fresh
   one and keeps spending model calls. Kill live reviewers, restore the default
   disposition, re-raise.
4. **A sourced `trap ... EXIT` clobbers the caller's own cleanup.** Chain it.
5. **An exported pool variable is inherited by child shells,** which skip init, adopt
   the parent's pool, and delete it on their own exit while the parent's reviewers are
   still running. Do not export it.

Two more that cost real seats: a trap **cannot fire while bash blocks in a foreground
child** (background each reviewer and `wait`), and `auth.json` must be copied alongside
`config.yaml`/`.env` or OAuth-based providers fail with "No … OAuth credentials stored"
while API-key providers succeed — a partial credential failure that reads like a model
outage.

**Rejected alternatives — do not reach for these:**

| Approach                         | Why it fails                                                                                                                                        |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| bare `mktemp -d`, unseeded       | No credentials: `HTTP 401: Missing Authentication header` **with exit code 0**, so a fan-out silently scores dead reviewers as successful seats.    |
| a dedicated named profile + `-p` | Works, but litters the profile namespace with entries needing sweep-on-crash and forces invented names. Nested names fail `rc=2` with empty output. |
| MoA presets                      | MoA broadcasts **one** prompt to N models. A panel needs N **different** prompts. Different feature.                                                |
| `delegate_task` per-task model   | Upstream has declined it repeatedly (PRs #17718, #23266, #25026, #34773, #36790). It will not arrive — do not design around it.                     |

### Running reviewers as Hermes one-shots

Use `reviewer_run` from the isolation helper above. Do not write a bare `hermes -z`
command for a reviewer. The CLI defines `-z/--oneshot` as `-z PROMPT`, so the prompt
must be the argument immediately after `-z`; placing options there yields
`argument -z/--oneshot: expected one argument`. The helper keeps the valid shape fixed
and also handles credential seeding, per-seat state, and provider pinning.

The cleanest way to run an independent reviewer is a headless `hermes -z` call against a
chosen provider/model. Confirm the local profile actually has the provider before using
it: `hermes config get model.providers` (or read `~/.hermes/config.yaml`).

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
3. **Set the timeout explicitly on every reviewer call. Never inherit the default.**
   Review models take longer than normal chat, especially with long prompts or slow/deep
   models — but an agent terminal tool's default timeout is typically sized for ordinary
   shell commands (a few minutes at most) and will cut a healthy reviewer off long
   before it finishes. **An omitted timeout is not "the value this skill recommends," it
   is whatever the environment happens to default to.** Pass it on the tool call every
   time, scaled to scope:

   | Scope                                                | Timeout                      |
   | ---------------------------------------------------- | ---------------------------- |
   | Single small artifact, one lens                      | 300s                         |
   | Normal review, multi-file or multi-lens              | 300-600s                     |
   | Deep panel, slow/reasoning models, large brief       | 600s                         |
   | Anything you expect to exceed the foreground ceiling | background + poll, no fg cap |

   Foreground tool calls usually have a hard ceiling of their own (commonly around
   600s), so a review genuinely bigger than that must run as a background process and be
   polled — not squeezed into a foreground call that will be killed. Check your
   runtime's actual foreground maximum rather than assuming 600s is available.

   If a run still times out, first try to recover the coverage — shrink the artifact,
   split the panel, or replace the seat — rather than accepting the loss. Only when that
   fails does the degradation rule in the Core Contract apply: synthesize solely if the
   completed seats still meet this target's depth floor, and label the gap. Never
   silently fall back to a partial review.

4. **Use `--ignore-rules` deliberately, and re-inject any safety rules you still need.**
   It stops the calling profile's persona from washing out the review lens — but it also
   strips project rules. If the artifact may contain private data (real names, host
   paths, ports, secrets, internal context) or you're operating in a repo with a
   privacy/PII policy (for example an `AGENTS.md` zero-PII block), **copy those
   constraints into the reviewer prompt** so the headless reviewer doesn't echo
   sensitive data into its output or any follow-up text. Independence of lens, not loss
   of safety.
5. **Confirm the provider exists first** with `hermes config get model.providers` (or
   read `~/.hermes/config.yaml`) before selecting it.

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

7. **Have every reviewer write findings to a file as it goes, not only at the end.** A
   reviewer that is killed — by a timeout, a wedged tool call, a crashed child, a
   dropped connection — takes everything it found with it if its only output channel is
   the final return value. This is a real and expensive failure: a review can identify a
   genuine bug and then die before reporting it, leaving no trace that the bug was ever
   seen. Give each seat its own output file and have it append findings incrementally,
   highest-severity first, so a partial file is still useful evidence. Then, when a seat
   fails, **read its partial file before declaring the seat lost.** Findings recovered
   this way are real findings — carry them into synthesis, attributed to a seat marked
   incomplete, and let the depth floor decide whether the panel still stands. Never
   discard a dead reviewer's output unread.

   This applies to every execution path, but it matters most where you do not control
   the deadline (path 1): a file on disk is the only thing that survives a child the
   runtime decides to kill.

8. **Never trust a reviewer's self-report about its own tool failures — stat the file.**
   When a reviewer says it could not read a seat's output, that its input was empty, or
   that a file was 0 bytes, verify the claim against the filesystem before acting on it.
   Self-reported tool failures are biased: they fail in the direction that flatters the
   reporter, because "the input was missing" excuses an omission that "I did not read
   it" would not. This is not theoretical — in a real panel, a synthesizing model
   reported a peer's critique as "a 0-byte file" when the file on disk was over 7KB, and
   the dropped objection was the one that would have caught a regulatory risk on its own
   top-ranked recommendation. Check size and mtime yourself. A synthesis built on an
   unverified claim of missing input is not a synthesis, and the seat it silently
   dropped was usually the disagreeing one.

```bash
# Provider/model names are PLACEHOLDERS — resolve them from the local config.
# Suitable for normal-sized artifacts; for large inputs, chunk or send a bounded brief.
# When running these through an agent terminal tool, ALWAYS pass the timeout explicitly
# (see execution rule 3): ~300s normal, ~600s deep/slow panels, background+poll beyond
# the foreground ceiling. An omitted timeout inherits the environment default, which is
# usually sized for ordinary shell commands and will kill a healthy reviewer early.
export HERMES_HOME=/path/to/the/active/profile   # if not already inherited
source "$SKILL_DIR/scripts/reviewer_home.sh"
reviewer_pool_init
reviewer_run "$PROMPT_GROK"   --provider <grok-provider>   -m <current-grok-model>   --ignore-rules &
reviewer_run "$PROMPT_GEMINI" --provider <gemini-provider> -m <current-gemini-model> --ignore-rules &
reviewer_run "$PROMPT_GPT"    --provider <gpt-provider>    -m <current-gpt-model>    --ignore-rules &
wait
reviewer_pool_destroy
```

Do not copy a version number out of this document. Resolve each family's **current**
flagship from the local config or provider listing — hard-coded slugs go stale and
silently 404.

If the profile exposes a router combo/alias for a family, prefer the **combo name** over
a raw slug. A subscription-backed combo (for example an OAuth seat with a pay-per-token
fallback) prefers subscription quota, but **can still fall back to metered usage** — so
verify routing and cost policy rather than assuming a review is free.

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

1. **Recursively copying an active Hermes profile for reviewer isolation.** A profile
   contains `state.db`, caches, and outputs; one seat can be gigabytes. Four concurrent
   `cp -r ~/.hermes/profiles/kenbot` reviewers filled a 96 GB root disk on 2026-08-26
   and caused SQLite EIO. Use `scripts/reviewer_home.sh`; seed only `config.yaml`,
   `.env`, and `auth.json`.
2. **Calling the sourced helper as an executable.** `bash reviewer_home.sh seat` prints
   nothing because the file only defines functions. Source it, call
   `reviewer_pool_init`, then call `reviewer_run`; never catch an empty result by
   inventing an unseeded home.
3. **Putting options between `-z` and its prompt.** `-z` consumes exactly its next
   argument as `PROMPT`; `hermes -z --model ... "$PROMPT"` fails at argument parsing. Do
   not hand-write reviewer CLI invocations; `reviewer_run "$PROMPT" [options]` preserves
   the one valid order.
4. **Calling it multi-model when it was not.** If only one model family ran, stamp the
   run as degraded.
5. **Letting reviewers see each other first.** This creates anchoring. Run independent
   reviewers before synthesis.
6. **Over-fixing theoretical issues.** Good review reduces risk; it should not turn
   clear work into defensive sludge.
7. **Skipping meta-review.** The synthesis can be worse than the raw findings if it
   blindly accepts noise.
8. **Using the same panel for every task.** Code, comms, plans, and rollouts fail in
   different ways.
9. **Forgetting the approval gate.** A review can recommend an action, but irreversible
   changes, public sends, secrets, money, and broad rollouts still need human approval.
10. **Ignoring missing setup.** If the environment lacks Grok/Gemini/GPT routing, fall
    back honestly and note how to improve the panel next time.
11. **Confusing optimization with defect discovery.** The goal is meaningful risk
    reduction, not generating feedback for its own sake.
12. **Letting `--ignore-rules` strip safety, not just persona.** It also removes project
    privacy/PII rules. Re-inject any privacy or safety constraints the artifact needs
    into each reviewer prompt before ignoring rules.
13. **Delegating open-ended I/O-heavy review work.** Subagents are best for bounded
    reasoning. For crawls, searches, downloads, or broad file inspection, gather and
    reduce in the parent first, then send reviewers a self-contained brief.
14. **Committing private routing notes to a public skill.** Profile-specific provider
    aliases, private endpoints, key env names, and owner-specific panel recipes belong
    in private profile references, not the public repo. Public guidance should describe
    the pattern and safety constraints, not private infrastructure.
15. **Letting reviewer seats inherit the caller's side-effecting tools.** `-t ''` asks
    for text-in/text-out but does NOT reliably strip tools the profile resolves on its
    own. Measured 2026-08-29 on a Hermes Kanban worker: two seats inherited the kanban
    toolset and `HERMES_KANBAN_TASK` from the copied `config.yaml`/`.env`, and both
    "delivered" their review by calling `kanban_complete` on the CALLER'S OWN CARD —
    marking in-progress work `done` while it was still in progress, and writing a
    comment as the worker. Their review content was genuine; the completion was an
    artifact of the harness. A reviewer must not be able to mutate shared state. Before
    fanning out, strip or neutralise any board/messaging/cron identity in the seeded
    scratch home (unset the task/session env vars, or seed a `config.yaml` with those
    toolsets disabled), and afterwards **verify the shared state you own is still in the
    state you left it** — the seat's own report will not mention what it touched.

    **Recurred 2026-09-06, worse, and the extra damage is the lesson.** A seat again
    called `kanban_complete` on the caller's live card — but this time the summary it
    wrote was **fabricated end to end**: it claimed the PR was "merged as `bb2542a1`"
    (that object does not exist), listed two changed files that do not exist in the
    repo, reported "15032 passed, 0 failed" against a real 4-failed run, and invented a
    root cause for the very bug under investigation. It also **deleted the task
    workspace directory** and **hand-edited two source files** — the file under review
    and an unrelated live-money module it was never pointed at. Three additions:
    - **The guard must cover EVERY seat, including throwaway ones.** The panel script
      here correctly unset the board vars; the seat that fired was a one-line
      connectivity probe run afterwards to debug why other seats returned empty. A probe
      is a full agent. There is no such thing as a seat too small to need the guard.
    - **A seat outlives its runner.** Killing the panel script left a seat still running
      and still editing the working tree minutes later. After any panel:
      `pgrep -f 'hermes -z'` and kill survivors, then `git status` and diff every
      unexpected path. Files a seat touched look exactly like your own uncommitted work.
    - **Treat every factual claim in a seat's completion as unverified.** A fabricated
      handoff is worse than a missing one: a downstream worker reads "merged `<sha>`" as
      fact. Check merge claims with `gh pr view --json state,mergeCommit` and
      `git cat-file -t <sha>`, and check file claims with `ls`, before believing or
      forwarding anything a seat wrote to durable state.

    The safest shape remains the one this skill already prescribes: point seats at an
    **exported diff file**, never the live branch directory.

16. **Treating a killed reviewer as a reviewer that found nothing.** A seat cut off by a
    timeout has usually done real work. If it wrote nothing incrementally you have no
    way to know, which is why rule 7 exists; if it did, read the partial file before
    writing the seat off.
17. **Accepting a self-reported tool failure at face value.** "The file was empty" is a
    claim, not an observation. Stat it. See rule 8.
18. **Staffing a multi-seat panel entirely with verifiers.** An all-frontier panel
    converges: it produces a well-written consensus and misses the objection nobody
    listed. Seat a divergence source (open-weight models are the measured pick) and let
    the verifiers grade it. This applies to real panels; a single-seat quick check is a
    documented degraded path, not a violation of it.
19. **Treating an open-weight seat's novel finding as a result.** Those seats generate
    the new material and verify almost none of it. Novel plus unverified is a lead;
    label it that way until a verifier checks it.
20. **Counting a seat as family coverage without checking which model answered.** A
    route can silently fall back to a different underlying model mid-run and keep
    answering as if nothing changed, which quietly collapses your model diversity to one
    family while the report still claims several.
21. **Assuming a reviewer found your prior decisions.** It almost certainly did not.
    Paste the rejected-approach graveyard and settled constraints into the brief, or
    expect a confident re-proposal of something you already killed.
22. **Promoting a model to a panel seat on reputation.** A newer flagship marketed as a
    reasoning upgrade may not beat the model it supersedes on your work. Score it on a
    task you have already measured first.

## Verification Checklist

- [ ] Target, audience, and stakes identified
- [ ] Depth chosen based on risk
- [ ] Lenses selected for the scenario, not from habit
- [ ] At least two model families used when available (or degradation stamped if not)
- [ ] Privacy/PII constraints re-injected into every isolated reviewer prompt
- [ ] Panel staffed for both generation and verification (2+ seats), or single-seat run
      stamped degraded with both roles carried in one prompt
- [ ] Prior decisions / rejected approaches pasted into the reviewer brief
- [ ] Confirmed which model actually answered each seat before claiming family coverage
- [ ] Reviewer seats cannot mutate shared state (board/messaging/cron identity stripped
      from the scratch home), and shared state was re-checked after the fan-out
- [ ] Every reviewer call passed an explicit timeout scaled to scope (never inherited)
- [ ] Every reviewer used `scripts/reviewer_home.sh` by sourcing it and calling
      `reviewer_run`; no direct execution, unseeded fallback, or recursive profile copy
- [ ] `HERMES_HOME` or `REVIEWER_SOURCE_HOME` named the active credential-bearing
      profile
- [ ] No hand-written `hermes -z` reviewer command placed options before the prompt
- [ ] Each seat wrote findings to its own file incrementally
- [ ] Any failed seat's partial output was read before the seat was declared lost
- [ ] Reviewer claims of missing/empty input were verified against the filesystem
- [ ] Reviewers ran independently
- [ ] Findings synthesized into auto-fix / ask / defer / wontfix
- [ ] Safe auto-fixes applied when authorized
- [ ] Material fixes re-reviewed
- [ ] Meta-review completed
- [ ] Final report labels model/panel degradation honestly
