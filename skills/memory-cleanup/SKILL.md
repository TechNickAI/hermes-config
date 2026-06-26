---
name: memory-cleanup
description: >
  Use when a Hermes MEMORY.md or USER.md file is too large, bloated, stale, or over the
  recommended cap and you need to reduce prompt footprint without losing important facts.
  Applies a lossless memory diet: compress, relocate to SOUL.md/USER.md/AGENTS.md/context
  files, offload long-tail facts to the memory provider, convert reusable procedures into
  skills, then drop only stale or duplicate material. Includes dry-run, diff, and review
  gates before any write.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [memory, cleanup, prompt-budget, curation, skills]
    related_skills: [recall, multi-review]
---

# Memory Cleanup

## Overview

Hermes core memory is deliberately small. `MEMORY.md` and `USER.md` are injected into the
prompt at session start, so every stale sentence is paid for on every turn of that session.
The goal of this skill is to cut memory size, often by half, **without losing data or
important instructions**.

This is not a deletion pass. It is a routing pass. Each fact moves to the smallest correct
home:

1. **Compress in place** when the fact truly belongs in core memory.
2. **Relocate** to a better always-on file (`SOUL.md`, `USER.md`, a project `AGENTS.md`, or a
   context file) when the fact is not agent-memory.
3. **Offload** long-tail searchable context to the configured memory provider when it should
   be retrievable but not always in the prompt.
4. **Convert to a skill** when the entry is a reusable procedure, workflow, or hard-won
   how-to.
5. **Drop** only when the entry is stale, duplicated, already captured elsewhere, or will be
   wrong soon.

Default mode is **dry run**. The agent running this skill must not edit live memory until the
proposed diff has been reviewed and approved.

## Target Parameter

This skill accepts an optional cleanup target that sets how aggressive the diet should be.
Accept it in whatever form the user gives it and normalize to an absolute character goal for
`MEMORY.md`:

- A percentage, for example `50%`, means cut `MEMORY.md` to half its current size.
- An absolute size, for example `5000` or `5000 chars`, means get `MEMORY.md` at or under
  that many characters.
- A word like `aggressive`, `moderate`, or `light` maps to roughly 60, 40, and 20 percent
  reduction.
- No target given means default to the recommended cap (about 2,200 chars for `MEMORY.md`),
  or a 40 to 60 percent reduction if the file is already near the cap.

Normalize at the start of the run:

```text
current = chars(MEMORY.md)
goal_chars =
  if target is "<n>%":        round(current * (1 - n/100))
  if target is "<n>" or "<n> chars": n
  if target is a word:        current * (1 - {aggressive:0.6, moderate:0.4, light:0.2})
  if no target:               min(2200, round(current * 0.5))
```

State the resolved goal explicitly in the output, for example
`target: 50% (MEMORY.md 4800 -> goal 2400 chars)`. Apply the target to `MEMORY.md`. Treat
`USER.md` as cleanup-by-correctness, not by quota: move and compress what belongs, but do not
force it under an arbitrary size if every line is a live preference.

If the target cannot be met without dropping load-bearing content, stop at the safe minimum,
report the gap, and recommend an offload-to-provider or skill conversion to close it. Never hit
a character goal by deleting something important. The size target is a goal, the no-data-loss
rule is a hard constraint, and the constraint always wins.

## When to Use

Use this skill when:

- `MEMORY.md` or `USER.md` is near or over its cap.
- Core memory has grown with long procedures, project logs, stale incidents, or repeated
  corrections.
- The user asks to reduce memory size without losing data.
- A profile uses an inflated memory cap and should move back toward the recommended bound.
- You need to decide whether a fact belongs in memory, user profile, persona, project rules,
  external memory, or a skill.

Do not use for:

- One-off deletion of an obviously wrong line (just remove it).
- Fast-changing task progress. That belongs in session history or a project tracker, not
  memory.
- Editing public repo files with private memory content. Keep real memories in private
  dry-run artifacts only.

## Recommended caps

The documented default caps are about 2,200 chars for `MEMORY.md` and 1,375 for `USER.md`.
A profile may have a higher configured cap, but the recommended targets above are what keep
memory sharp. Confirm the live values per profile with `hermes memory status` or the
profile's `config.yaml` before deciding a target.

## Classification Ladder

Process every entry through this ladder in order. When more than one destination is valid,
use this precedence and pick the smallest always-loaded scope:

> user fact > persona rule > project rule > external provider, and procedures always become
> skills.

Concretely: if a line is about the user, it goes to USER.md, not MEMORY.md. If it is a global
behavior or voice rule, it goes to persona, not project. If it is project-specific, it goes to
that project's file, not global memory. Use the provider only after confirming the fact is
neither always-on nor a procedure.

### 1. Keep and compress in core memory

Keep in `MEMORY.md` if it is a durable, high-signal fact about the agent's environment, stable
tooling, or a recurring correction that prevents future mistakes.

Keep in `USER.md` if it is a durable fact about the user: communication preferences, work
style, standing dislikes, or stable expectations.

Compression tactics:

- Merge overlapping entries.
- Replace prose with compact declarative facts.
- Remove dates unless the date changes interpretation.
- Remove examples if the rule is clear without them.
- Use nouns, not instructions, where possible.

Generic example, before:

```text
On a recent task the agent reported a build had finished when the build process had actually
crashed, and the user had to point this out. The agent should always confirm a process
completed before reporting success.
```

After:

```text
Report a process as complete only after confirming it with a real check (exit code, output, or
process status) in the same step.
```

### 2. Move to SOUL.md or persona rules

Move there when the fact defines how the agent should sound or behave globally: voice, tone,
persona, and hard interaction preferences (formality level, verbosity, punctuation or
formatting rules).

Keep only the most compact pointer in `USER.md` if the preference is user-specific and must
remain visible to non-persona contexts.

### 3. Move to USER.md

Move from `MEMORY.md` to `USER.md` when the entry is about the user rather than the agent's
environment: preferences, work style, approval style, risk tolerance, or repeated corrections
about communication.

### 4. Move to AGENTS.md, project rules, or context files

Move there when the fact is tied to a repo, project, or stack rather than the agent globally.

Generic examples:

- Repository-specific PR routing rules.
- A repo's privacy or contribution policy.
- Test commands for one codebase.
- Deployment conventions for one stack.

Core memory may keep a one-line pointer if the project is frequently used:

```text
For project X, read its AGENTS.md before editing; it has the repo-specific contribution and
review rules.
```

### 5. Offload to the memory provider

Use this for long-tail facts that should be searchable but not always injected: background
research, detailed history, past incidents, large inventories, and old run logs. If the
profile has an external memory provider configured, store or index the full detail there.

Keep a compact pointer in core memory only when future use depends on remembering that the
offloaded knowledge exists. If the detail is discoverable through a normal workflow (a search,
an index, a status command), do not keep a pointer at all.

A pointer is valid only if it names a real destination. Before replacing concrete detail with
a pointer, verify the destination exists and contains the detail. Bad pointer: "look in the
relevant skill." Good pointer: "Load skill `<exact-skill-name>`; it contains the commands and
rollback notes for this procedure." If no destination exists yet, create it first or keep the
critical detail in core memory.

### 6. Convert to a skill

Use a skill when the entry is a procedure: it says how to do a recurring task, or contains
commands, ordered steps, pitfalls, or verification gates.

Before removing the memory entry, the new or updated skill must preserve, not summarize:

- the exact commands,
- the ordered steps and decision points,
- the pitfalls and edge cases,
- the verification gates,
- the trigger conditions for when to use it.

A good memory diet often creates or updates a skill, then replaces a long memory entry with one
pointer:

```text
For task Y, load skill Z; it has the full procedure and pitfalls.
```

### 7. Drop

Drop only if the entry is:

- obsolete or superseded,
- duplicated elsewhere,
- completed task progress,
- an artifact ID, PR number, or commit SHA that will be stale soon,
- a one-off incident with no reusable pattern,
- already captured exactly in a better home.

If you are not sure, do not drop. Classify as keep/compress or offload.

## Dry-Run Procedure

The agent running this skill must not edit live memory first. Produce a reviewable plan, then
apply only after approval.

1. Read the target files and byte counts:

```bash
wc -c "$MEMORY_DIR/MEMORY.md" "$MEMORY_DIR/USER.md"
```

`MEMORY_DIR` is the profile's memories directory (commonly `~/.hermes/memories`). Set it to the
correct path for the profile being cleaned.

2. Split entries on the section separator the files use, or the file's native structure.

3. For each entry, create a classification row with:

- entry id,
- current chars,
- classification (keep/compress, move, offload, skill, drop),
- proposed destination,
- proposed rewritten text or pointer,
- a short rationale for any non-obvious move/offload/drop,
- risk if wrong.

4. Write dry-run artifacts to a temporary scratch directory OUTSIDE the live memory directory:

```text
<scratch-dir>/
├── inventory.md
├── proposed-MEMORY.md
├── proposed-USER.md
├── relocation-plan.md
└── review-checklist.md
```

5. Measure the result:

```bash
wc -c "<scratch-dir>/proposed-MEMORY.md" "<scratch-dir>/proposed-USER.md"
```

The target is usually a 40 to 60 percent reduction in `MEMORY.md` while preserving every live
fact either in core memory or a named destination.

6. Review before write. Use `multi-review` for any memory file that affects future agent
behavior.

## Output Contract

A memory-cleanup dry run must produce:

```text
Target:
- Requested: <raw target, e.g. 50% or 5000 chars>
- Resolved MEMORY.md goal: <chars>

Before:
- MEMORY.md: <chars>
- USER.md: <chars>

After proposed:
- MEMORY.md: <chars> (<percent reduction>)
- USER.md: <chars> (<percent change>)

Moves:
- Keep/compress: <count>
- Move to USER.md: <count>
- Move to SOUL.md/persona: <count>
- Move to AGENTS.md/context: <count>
- Offload to provider: <count>
- Convert to skill: <count>
- Drop: <count>

Highest-risk decisions:
- <entry id>: <why risky, how reviewer should check it>

Files written:
- <artifact paths>
```

Do not claim data was preserved unless every original entry appears in the inventory with a
destination or an explicit drop reason.

## Applying the Cleanup

Only apply after approval.

Preferred apply path:

1. Back up live memory files.
2. Write any relocation target files first (SOUL.md, USER.md, AGENTS.md, context files, skills),
   then read them back and confirm the relocated content is semantically equivalent to the
   original before removing it from memory.
3. For provider offloads, verify retrieval works before deleting the full detail from core
   memory.
4. For skill conversions, write or update the skill, validate it, and confirm it preserves the
   commands and verification gates before replacing the memory prose with a pointer.
5. Update `MEMORY.md` and `USER.md`, using one atomic memory-tool batch when possible
   (remove/replace/add together).
6. Re-read byte counts to confirm the new sizes.
7. The always-on memory block in the system prompt is a snapshot taken at session start, so it
   refreshes on the next session even though tool responses show the live file immediately.
   Start a fresh session or reset to load the new snapshot.

## Review Checklist

Before applying, reviewers should verify:

- [ ] Every original entry is represented in the inventory.
- [ ] No standing user preference was moved out of USER.md without a replacement pointer.
- [ ] No safety-critical instruction was dropped.
- [ ] Procedures became skills that keep the original commands, steps, and verification gates,
      not vague reminders.
- [ ] Every pointer names a destination that exists and contains the detail (no "see the
      relevant skill" with no skill).
- [ ] Negative constraints ("do not modify X", "never touch Y") and concrete recipes (exact
      commands, known-good values) were preserved verbatim somewhere, not summarized away.
- [ ] Project-specific rules moved to the correct project file, not global memory.
- [ ] Provider offloads have a verified retrieval path, and a pointer only when needed.
- [ ] Relocated content was read back and confirmed equivalent before the original was removed.
- [ ] The proposed files meet the target reduction.
- [ ] The proposed files match the user's formatting and voice rules.
- [ ] The dry run clearly marks what is proposed, not already applied.

## Common Pitfalls

1. **Deleting instead of routing.** The goal is smaller prompt context, not forgetting. Most
   valuable text moves elsewhere.
2. **Moving user preferences into agent memory.** Preferences belong in USER.md or persona, not
   MEMORY.md.
3. **Turning a procedure into a tiny but useless sentence.** If it has steps, commands, or
   gotchas, make it a skill that preserves them in full.
4. **Trusting summaries.** A compressed line must preserve the decision-relevant content of the
   original. Review against the original entry, not against your memory of it.
5. **Offloading without retrieval.** If you move detail to a provider, verify it can be found
   before removing it from core memory.
6. **Applying mid-session and expecting the prompt snapshot to change immediately.** The
   always-on block refreshes next session; reset after applying.
7. **Committing private memory to a public repo.** The skill and examples must be generic. Real
   memory dry runs stay outside any shared repo.
8. **Pointing at a destination that does not exist.** Replacing a concrete recipe with "load the
   relevant skill" when no such skill exists loses the recipe. Create the destination first, or
   keep the detail. A pointer is only as good as its address.
9. **Hitting the size target by dropping load-bearing content.** The character goal is a target,
   not a license to delete. If you cannot reach it without losing important data, stop short and
   recommend an offload or skill conversion.

## Verification Checklist

- [ ] Dry-run artifacts written to a scratch dir outside the live memory directory.
- [ ] Every original entry accounted for (destination or explicit drop reason).
- [ ] Before/after byte counts reported; target reduction met.
- [ ] Relocations read back and confirmed before originals removed.
- [ ] Provider offloads verified retrievable.
- [ ] Skill conversions preserve commands and verification gates.
- [ ] Multi-review run on the proposed result before applying.
- [ ] Fresh session started after applying so the new snapshot loads.
