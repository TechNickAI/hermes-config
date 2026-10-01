---
name: user-profile-audit
description: >
  Use when auditing or cleaning a bloated, junk, or unsafe USER.md — the Hermes
  user-profile memory store. Covers "what should be in USER.md", a fleet-wide
  user-profile review, and the case where an agent has been dumping overflow into
  USER.md because MEMORY.md filled up. Scores a profile against a researched rubric,
  classifies every entry, and produces a dry-run rewrite. Read-only by default; never
  writes live memory without explicit approval.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [memory, user-profile, audit, privacy, prompt-budget, curation]
    related_skills: [memory-cleanup, multi-review]
---

# User Profile Audit

## Overview

`USER.md` answers exactly one question: **who is the human, such that the agent behaves
differently because it knows this?**

It is not a CRM record, not a dossier, not an overflow bucket for a full `MEMORY.md`.
Every character is injected into the system prompt at session start, on every session,
forever — so a stale line is paid for on every turn and a secret is transmitted to the
model provider on every request.

This skill audits that file. Default mode is **read-only dry run**: it classifies each
entry, scores the file, and proposes a rewrite. It does not touch live memory without an
explicit approval step.

### The failure mode this exists for

When `MEMORY.md` hits its character cap, an agent under pressure to "save this fact"
looks for somewhere it will still fit. `USER.md` has a separate budget. Facts that are
neither user-profile nor durable-agent-note get parked there — account numbers, file
paths, project mechanics, task state.

**A large or fast-growing `USER.md` on a profile whose `MEMORY.md` is at its cap is the
signature.** Audit both stores together; the size correlation is the diagnostic, not a
coincidence.

## When to Use

- A `USER.md` is bloated, stale, over cap, or visibly wrong.
- Reviewing user-profile hygiene across several agents.
- An agent has been writing overflow into `USER.md`.
- Deciding whether a specific fact belongs in `USER.md` at all.
- Before sharing, exporting, or distributing a profile (an export includes `memories/`
  and scans nothing for personal content).

Do **not** use for:

- `MEMORY.md` size problems → `memory-cleanup`.
- Removing one obviously wrong line → just remove it.
- Persona and voice → that is `SOUL.md`, a different file with different rules.

## Ground Truth: The Two Caps

| Store       | Upstream default cap | Config key                 |
| ----------- | -------------------- | -------------------------- |
| `MEMORY.md` | 2,200 chars          | `memory.memory_char_limit` |
| `USER.md`   | 1,375 chars          | `memory.user_char_limit`   |

Source: `tools/memory_tool.py` (`memory_char_limit: int = 2200`,
`user_char_limit: int = 1375`) and the upstream memory docs.

**Always resolve the effective cap per profile before judging a file.** A profile that
raised `user_char_limit` is not "over cap" at 3,000 chars — but a raised cap is itself
an audit finding, because the cap is the forcing function that keeps the file curated.
Report raised caps explicitly rather than silently scoring against them.

## What Belongs in USER.md

The test for every candidate line:

> **Would the agent behave differently on a future turn because it knows this?** If no,
> it does not belong. If yes, it belongs — in the smallest form that still changes
> behavior.

Two supporting tests, each of which independently rules out a failure class seen in real
profiles:

- **Would the user expect this to be sent to every model provider and tool, on every
  request?** This is what disqualifies secrets and account identifiers outright.
- **Is it still likely to be true in six months?** Durable facts qualify; anything
  point-in-time belongs in retrieval memory or session state.

Six categories qualify:

1. **Identity and role** — name, what they do, expertise level. Drives register and how
   much to explain.
2. **Communication preferences** — length, format, jargon tolerance, lead-with-answer,
   banned constructs.
3. **Standing expectations of the work** — what counts as done, what proof they want,
   how they want to be corrected.
4. **Autonomy and approval boundaries** — what to do without asking, what always stops
   for a yes.
5. **Corrections that recur** — a preference stated more than once. These are the
   highest-value entries in the file; they are literally what stops the human repeating
   themselves.
6. **Relationships and context that change assistance** — only where it alters behavior
   (a named partner whose requests carry authority; a non-technical stakeholder).

**Calibrate expertise per domain, not globally.** "Senior Go engineer, beginner at
frontend accessibility" is far more useful than "technical user."

## What Does NOT Belong

Ranked by how much damage it does.

### 1. Secrets and account identifiers — flag for immediate removal

Account numbers, card fragments, API keys, passwords, tokens, SSNs. This file is
transmitted to a model provider on every single request and is included verbatim in a
profile export.

Note that "last four digits" feels harmless and is not: combined with a name, address,
and phone number in the same file, it is an identity-theft kit and a social-engineering
key. **Nothing in this file should be usable to authenticate as the user.**

If an account mapping is genuinely needed for the work, the label belongs in the file
and the number does not: "the business account", not the digits. Authentication material
is never stored in a profile, a note file, or a knowledge base — it is retrieved at
execution time from a secret manager or a scoped integration, with the minimum fields
the task needs.

### 2. Overflow from MEMORY.md — reroute

Paths, commands, hostnames, ports, repo and branch names, schema details, deploy
mechanics. These are agent notes. Their presence in `USER.md` is the overflow signature.

Route by scope:

| Content                                         | Destination         |
| ----------------------------------------------- | ------------------- |
| Global environment facts, tool quirks, lessons  | `MEMORY.md`         |
| Repo-specific mechanics, commands, ports, paths | project `AGENTS.md` |
| A reusable multi-step procedure                 | a skill             |

### 3. Transient state — remove or re-home

Anything that decays: hard dates, "currently", "this week", PR and issue numbers, commit
SHAs, in-flight task status, active-project lists that churn.

A stale line does not merely waste budget — it actively misleads, because the agent
cannot tell a live preference from an expired one, and the failure is measured rather
than theoretical:

- **HorizonBench** ([arXiv:2604.17283](https://arxiv.org/abs/2604.17283), Apr 2026)
  tests 25 frontier models on preferences that change over 6-month histories. The best
  reaches 52.8%, most score at or below the 20% chance baseline, and **when models err
  on an evolved preference, over a third of the time they pick the user's originally
  stated value** rather than the updated one. The authors name state-tracking, not
  context length, as the bottleneck.
- **PrefEval** ([arXiv:2502.09597](https://arxiv.org/abs/2502.09597), ICLR 2025 oral)
  found zero-shot preference-following accuracy falling below 10% at merely 10 turns,
  and degrading even with prompting and retrieval.

So an undated or contradictory entry is not untidiness. It is the exact input shape
models are measurably worst at, sitting in the prompt on every turn. Supersede in place;
never leave two entries that disagree.

### 4. Dossier material — remove

Detailed personal history, biography, résumé entries, exhaustive career timelines, third
parties' private details, anything the user has not asked the agent to act on.

You are learning about a person to help them, not building a file on them. Career
history in particular is a common bloat pattern: it reads as useful context and almost
never changes a single downstream behavior.

### 5. Duplicates of other files — remove

Persona and voice belong in `SOUL.md`. Project rules belong in `AGENTS.md`. Procedures
belong in skills. `SOUL.md` and `USER.md` never feed each other, so content copied
between them is pure duplicated budget.

### 6. Facts available from an authoritative system — point, don't copy

If a system of record holds it and it changes, copying it into an always-on prompt
guarantees drift.

## PII: The Judgment Call

Contact details are not automatically wrong. An agent that sends mail on someone's
behalf needs their sending address; that is operational, and it earns its place.

Retain a personal field only when **all four** hold. If any fails, redact or drop it:

1. A **named, recurring** agent task needs that exact value.
2. A label or a just-in-time lookup **cannot** do the job instead.
3. The person the data describes is fine with it being stored this way.
4. The storage and export path is appropriate for that sensitivity.

Record the reason next to the decision — name the task that requires it and the
alternative you rejected. "It might be useful" is not a task.

Home address and personal phone rarely pass unless the agent books travel, sends
physical mail, or places calls. Account numbers and authentication material never pass,
regardless of convenience.

## How to Phrase Entries

Hermes' own tool guidance is explicit and it governs here:

> Write memories as declarative facts, not instructions to yourself. 'User prefers
> concise responses' ✓ — 'Always respond concisely' ✗.

The reason is mechanical: imperative phrasing gets re-read as a live directive in a
later session and can override the user's current request or trigger repeated work.

You will encounter contradicting advice. The OpenClaw user-model documentation
prescribes the opposite — imperative directives (`Always`, `Never`, `Prefer`) with
`<!-- observed: DATE | status: active -->` metadata — arguing that restating a
preference as a directive makes expected behavior explicit where the agent uses it.

**On Hermes, follow the Hermes rule.** The reconciliation that captures both concerns:
state the fact declaratively and attach the consequence to it.

```text
# Weak — a fact with no behavioral edge
Example User is an experienced engineer.

# Wrong on Hermes — imperative, re-reads as a directive
Always skip the basics and never use em dashes.

# Right — declarative, with the behavioral consequence attached
Example User is a veteran engineer and quant. Treat them as an expert peer: skip
basics, quantify tradeoffs, never use em dashes.
```

Additional rules:

- **One concern per entry.** Entries are edited by `replace` with substring matching. A
  600-char entry welding five preferences together cannot be surgically updated, so it
  gets rewritten wholesale or left stale. Target roughly 100–250 chars.
- **Never let the file become one entry.** A single-entry `USER.md` is unmaintainable by
  the tool that maintains it.
- **Supersede in place.** When a preference changes, edit that entry. Never append a
  second contradictory entry elsewhere.
- **Keep the evidence when it is short.** A quoted correction in the user's own words
  ("that's the opposite of what I want") is worth its characters — it is precise and
  survives paraphrase drift.

## Procedure

### 1. Collect and score

Run `scripts/audit_user_md.py` against the target profile home(s). It resolves the
effective caps from each profile's own `config.yaml`, measures both stores, flags a
raised cap, detects the overflow correlation, and prints the findings table.

```bash
python3 scripts/audit_user_md.py                      # $HERMES_HOME, or ~/.hermes
python3 scripts/audit_user_md.py ~/.hermes            # root + every named profile
python3 scripts/audit_user_md.py --json               # machine-readable
```

Exit codes: `0` no HIGH findings, `1` at least one HIGH finding, `2` nothing audited.
**`1` means "found something", not "failed"** — see the fan-out warning under Fleet Use.

Defaults when a profile sets no override: `USER.md` 1,375 and `MEMORY.md` 2,200. A
raised cap is itself a finding, since the cap is the forcing function that keeps the
file curated. If PyYAML is unavailable the script falls back to those defaults and the
reported cap may be wrong — check the profile's config directly before judging cap
findings on such a host.

| Severity | Code         | Meaning                                           |
| -------- | ------------ | ------------------------------------------------- |
| HIGH     | `SECRET`     | credential or account identifier                  |
| HIGH     | `OVER_CAP`   | exceeds the effective cap                         |
| MED      | `OVERFLOW`   | `MEMORY.md` at cap + misfiled notes in `USER.md`  |
| MED      | `PII`        | address, phone, email — needs the necessity test  |
| MED      | `TRANSIENT`  | dated, point-in-time, or decaying content         |
| MED      | `NEAR_CAP`   | ≥85% — overflow pressure                          |
| MED      | `MONOLITH`   | whole file is one entry                           |
| LOW      | `MISFILED`   | paths, commands, project mechanics                |
| LOW      | `FAT_ENTRY`  | >600 chars — not surgically editable              |
| LOW      | `IMPERATIVE` | phrased as an instruction, not a declarative fact |

Detectors are regex. They are **evidence, not verdicts** — every hit needs human
adjudication, and the detectors will both miss things and over-fire. A phone number is a
finding on a research agent and correct on an agent that places calls.

### 2. Read the file yourself

The scorer cannot judge whether a line changes behavior, whether a preference is stale,
or whether a quoted correction is worth its characters. Read every entry and apply the
behavior test by hand. Findings from step 1 are the starting point, not the audit.

### 3. Classify every entry

Assign exactly one disposition to every original entry — nothing is silently lost:

| Label      | Use when                                                        |
| ---------- | --------------------------------------------------------------- |
| `KEEP`     | passes the behavior test as written                             |
| `COMPRESS` | behavior-relevant but verbose, or welds several concerns        |
| `REROUTE`  | agent notes, project mechanics, procedures — name the target    |
| `REDACT`   | secrets and account identifiers; PII failing the necessity test |
| `DROP`     | transient, dossier, or duplicated elsewhere — state why         |

### 4. Write the dry run

To a scratch directory **outside** the live memory dir. Show before and after, char
counts, per-entry disposition, and the findings table.

### 5. Verify before proposing

- Every entry accounted for.
- Every `REROUTE` names a destination that exists.
- No `KEEP` line fails the behavior test.
- Result is under the effective cap.
- No **detector hit** survives in the proposed text. A clean scan is not a privacy
  clearance: these are regexes, and they miss private keys, JWTs, non-US identifiers,
  and quasi-identifiers that are only sensitive in combination. Read the proposed text
  yourself.

### 6. Apply only after approval

Back up the file first.

**Write to the correct profile.** The `memory` tool has no target-profile parameter: it
resolves its path from `HERMES_HOME` at call time (`tools/memory_tool.py`,
`get_memory_dir()` / `_path_for()`), so it always writes the **calling** agent's store.

| Target                         | How to apply                                                |
| ------------------------------ | ----------------------------------------------------------- |
| The profile you are running as | one atomic `memory` batch (removes + adds in a single call) |
| Any other profile              | edit that profile's `memories/USER.md` file directly        |

Using the `memory` tool while auditing someone else's profile overwrites **your own**
`USER.md` with their content. That is silent, immediate, and damages two profiles at
once. When editing another profile's file directly, keep the `§` delimiter format
exactly — the memory tool refuses to write a file whose content will not round-trip
through its parser, and a malformed file blocks that agent's future memory writes.

Use one atomic batch where the tool applies, because the char limit is evaluated on the
final state: a removal and an addition together can succeed where the addition alone
would overflow.

The system-prompt block is a **frozen snapshot** taken at session start, so the file on
disk changes immediately and the prompt does not. Start a fresh session to load the new
snapshot. Do not "verify" by reading the current session's memory block.

## Fleet Use

The script discovers profiles **on the machine it runs on** (`~/.hermes` plus every
`~/.hermes/profiles/<name>/` with a `memories/` dir). It has no remote capability. To
cover several machines, copy the script to each and run it there, then collect the
`--json` output centrally.

When auditing many agents:

- Run the collector across **every profile you intend to judge** before drawing
  conclusions about any single one. A finding that appears on one agent and no other is
  usually that agent; a finding on all of them is usually your rubric.
- Each agent's `USER.md` describes **its own** operator. Never normalize them toward one
  voice, and never copy an entry between profiles.
- Enumerate profile directories in code. A shell glob aborts or silently undercounts
  when a path does not match, which reports a clean fleet that was never measured.
- **The audit output is itself personal data**, not just the profiles. Raw `USER.md`
  content, findings with evidence, proposed rewrites, backups, and dry-run files must
  stay local: never in a shared repo, an issue tracker, CI artifacts, or a group chat.
  Delete them when the audit is done.
- **Audit only profiles you are authorized to audit**, for a stated purpose. Prefer
  reporting per-profile findings over centralizing raw profile text, and involve the
  profile's owner when the findings concern their personal data.
- **Exit code 1 means "HIGH findings", not "failed."** A fan-out wrapper written as
  `preferred_python script.py || fallback_python script.py` re-runs the script on
  exactly the profiles that have a HIGH finding, concatenates two JSON documents, and
  the parse error then reads as an unreachable host. The worst profile in the fleet is
  the one that disappears from the report. Choose the interpreter first, then run the
  script once.
- An agent that has never written a user profile has no `USER.md` at all. That is
  "nothing to audit", not a failure, and should be reported distinctly from unreachable.

## Common Pitfalls

1. **Deleting instead of rerouting.** Most flagged content is real and simply misfiled.
   Route it; do not destroy it.
2. **Trusting the detectors.** They are regex. The necessity test is a judgment call and
   the scorer cannot make it.
3. **Scoring against the wrong cap.** Read the profile's own config; a raised cap is a
   finding, not a license.
4. **Auditing `USER.md` alone.** Without `MEMORY.md` sizes you cannot see the overflow
   pattern that caused the mess.
5. **Flattening a whole file into one entry** to save delimiter characters. It saves
   almost nothing and destroys editability.
6. **Removing an entry because it is unusual.** An idiosyncratic, hard-won correction is
   the highest-value content in the file.
7. **Confirming a write from the current session's prompt block.** Frozen snapshot —
   re-read the file, or start a new session.
8. **Committing real memory content to a public repo.** Examples in a shared skill must
   be synthetic.
9. **Assuming a redaction happened because you proposed it.** Re-scan the applied file.
10. **Using the `memory` tool to fix a different profile.** It writes to whatever
    `HERMES_HOME` currently resolves to, so it silently rewrites the auditing agent's
    own profile with someone else's content. Edit the other profile's file directly.

## Verification Checklist

- [ ] Effective caps resolved per profile from that profile's own config
- [ ] Both stores measured; overflow correlation checked
- [ ] Every entry has a disposition
- [ ] Every HIGH finding adjudicated, not just reported
- [ ] PII necessity test applied per field, with reasons recorded
- [ ] Proposed file re-scanned with no detector hits, read by a human, and under cap
- [ ] Dry run written outside the live memory directory
- [ ] Approval obtained before any write
- [ ] Backup taken; atomic batch used; file re-read after applying
- [ ] Fresh session started before claiming the change is live
