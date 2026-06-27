# Memory Cleanup

A skill for putting a Hermes agent's core memory on a diet, often cutting it in half,
without losing data or important instructions.

## Why this exists

Hermes core memory (`MEMORY.md` and `USER.md`) is injected into the system prompt on
every turn. That is what makes it powerful: the agent always knows your preferences and
its environment. It is also what makes it expensive: every stale sentence is re-sent on
every API call, for the life of the conversation.

The framework caps these files on purpose (the recommended defaults are 2,200 chars for
`MEMORY.md` and 1,375 for `USER.md`). The cap is not a limitation to work around. It is
the mechanism that keeps memory accurate. To add a fact, something else must give, so
every line competes on durability and usefulness. Raising the cap removes that pressure
and lets memory rot: contradictory lines survive, procedures pile up, old incidents
linger, and the agent's always-on context slowly fills with noise.

When a profile has drifted above the recommended cap (sometimes many times over), the
right fix is not a wider budget. It is a diet. But a naive diet just deletes lines and
loses real knowledge. This skill provides a disciplined, lossless alternative.

## The core idea: route, do not delete

Most bloated memory is not junk. It is the right information in the wrong place. The
skill runs every entry through a ladder and sends each fact to the smallest correct
home:

1. **Compress in place** when it truly belongs in core memory.
2. **Relocate** to a better always-on file:
   - `SOUL.md` for persona, voice, and hard behavioral rules.
   - `USER.md` for user identity and preferences.
   - a project `AGENTS.md` or context file for repo-specific or stack-specific rules.
3. **Offload** long-tail searchable detail to the configured memory provider so it is
   retrievable without being always in the prompt.
4. **Convert to a skill** when the entry is really a reusable procedure or workflow.
5. **Drop** only what is stale, duplicated, or already captured elsewhere.

The result is a much smaller always-on footprint with no loss of knowledge, because
almost everything moves rather than disappears.

## Why each destination matters

- **SOUL.md and persona**: voice and behavior rules are read on every message anyway.
  Keeping them in memory double-pays and competes with facts for the cap.
- **USER.md**: preferences about the user belong in the user profile, not in the agent's
  environment notes. Mixing them wastes the memory budget and blurs the two stores.
- **AGENTS.md and context files**: project-specific rules should travel with the
  project, not sit in global memory where they apply even when you are working on
  something else.
- **Memory provider**: history, research, and large inventories should be searchable,
  not permanently resident in the prompt. This is exactly what providers are for.
- **Skills**: a procedure compressed into a one-line memory reminder loses its steps and
  becomes useless. As a skill it stays complete and loads only when relevant.

## Safety model

- **Dry run by default.** The skill produces a proposed before/after and a relocation
  plan. It does not touch live memory until the plan is reviewed and approved.
- **Account for every entry.** Each original line must appear in the inventory with a
  destination or an explicit drop reason. "Preserved" is only true if you can point to
  where each fact went.
- **Review gate.** Run `multi-review` on the proposed result. Memory shapes future
  behavior, so it deserves a second and third set of eyes.
- **Apply atomically, then reset.** Memory is loaded once at session start, so apply the
  change and start a fresh session for it to take effect.

## How to use it

Load the skill and point it at the memory files. Optionally pass a cleanup target, for
example `50%` or `5000 chars`, to set how aggressive the diet should be (no target
defaults to the recommended cap). The skill reads the files, classifies every entry,
writes dry-run artifacts to a scratch directory outside the live memory folder, and
reports a before/after with the full set of moves. Review, approve, then apply.

See `SKILL.md` for the full procedure, the classification ladder, the output contract,
and the review checklist.

## A note on privacy

Real memory content is personal. Keep dry-run artifacts and any real `MEMORY.md` content
out of shared or public repositories. This skill and its examples are intentionally
generic.
