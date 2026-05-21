# Skill System Deep Dive

> Conclusion first: Hermes skills are **procedural memory the agent writes for itself**,
> not scripts you install. The self-improvement loop creates them from successful
> problem-solving; the Curator promotes them through `active → stale → archive` based on
> whether they keep being useful. Your job is to seed a few good starters and then get
> out of the way — the agent's skills will be better than yours by day 30.

This is the deep dive for the skill subsystem described at a structural level in
[hermes-architecture.md](hermes-architecture.md) and contrasted against OpenClaw's
marketplace model in [hermes-vs-openclaw.md](hermes-vs-openclaw.md).

## Three sources of skills, ranked by where the long-term value comes from

| Source                        | Where it lives                            | Who writes it                   | Long-term share of your skill set  |
| ----------------------------- | ----------------------------------------- | ------------------------------- | ---------------------------------- |
| **Agent-authored**            | `~/.hermes/skills/<name>/` (auto-created) | The agent, via self-improvement | Highest                            |
| **Hermes Skills Hub curated** | Ships with Hermes; usable out of the box  | The Hermes team (high-quality)  | Medium                             |
| **Hand-curated (this repo)**  | `skills/` in this repo                    | You, copied into `~/.hermes/`   | Lowest — starters, not destination |

The shift from OpenClaw's "marketplace" mental model to Hermes' "garden you tend" model
is the single most important thing to internalize about this subsystem.

## The self-improvement loop

The loop is the trigger condition for a new skill being born.

### When skills get created

The agent crystallizes a skill when:

1. It struggles through a multi-step problem (research, experimentation, dead-ends).
2. The problem is **solved successfully** — failed problems don't become skills.
3. The solution is **reusable** — one-off fixes don't get crystallized.
4. The crystallization is **autonomous** — the user doesn't have to ask; the agent
   decides.

What "the agent decides" looks like in practice: after a successful turn, an
asynchronous background pass examines the trajectory. If the problem looked novel and
the solution looked repeatable, the agent writes a skill: a markdown note describing the
steps, the gotchas, and the conditions under which to apply it.

### What a fresh skill looks like

Roughly (the shape evolves; check the live source for canonical structure):

```markdown
# <Skill name>

When to use: <one-line trigger condition>

Steps:

1. <Step the agent figured out>
2. <Step the agent figured out>
3. <Verification or output expectation>

Gotchas:

- <Thing that almost broke>
- <Thing that did break the first time>
```

Notice what's missing: code. The skill is a prompt-shaped reminder, not a script. When
the agent next encounters the trigger condition, the skill enters the system prompt and
guides the agent through the same procedure — but with the freedom to adapt to the
current context.

This is why Hermes calls it **procedural memory**. It's the agentic equivalent of "I've
solved this before, here's how."

## The Curator

The Curator is a background agent that runs periodically and maintains the skill set.
Its job is the opposite of creation: pruning.

### Lifecycle states

Every skill carries a state: `active`, `stale`, or `archive`.

- **`active`** — Recently created or recently used. Loaded into the agent's system
  prompt context when relevant.
- **`stale`** — Hasn't been used in a while. Still on disk, but not eagerly loaded. The
  Curator considers archiving it.
- **`archive`** — Demoted, not in the working set. Recoverable but invisible by default.

### Promotion / demotion criteria

The Curator looks at:

- **Usage frequency** — does this skill get invoked?
- **Quality of outcomes** — when invoked, does the resulting turn succeed?
- **Overlap with newer skills** — has a better/newer skill superseded this one?
- **Drift** — does the skill describe a procedure that no longer matches reality (e.g.
  the tool it references has been removed)?

The exact thresholds shift; the Hermes team tunes them. The point is that the Curator
gives the skill set a metabolism, so it doesn't accumulate forever the way OpenClaw
skills did.

### When to expect Curator runs

The Curator runs in background async passes — periodically during normal use, and
explicitly via `/curator` (or whatever the slash command surface evolves to). You don't
have to think about it. If you notice your skills drifting (stale entries cluttering
`~/.hermes/skills/`), running it manually is harmless.

## When NOT to write a skill

A few cases where a skill is the wrong tool:

- **One-off operations** — don't create a skill for a thing you'll do once.
- **Personal preferences** — those belong in `user.md`, not skills.
- **Hard limits / constraints** — those belong in `SOUL.md`, not skills.
- **Knowledge that drifts fast** — fast-changing API docs belong in a context file or a
  fresh web search, not a frozen procedure.

A skill is for **how to do something** when there's a repeatable answer. Anything else
is a different tool.

## Agent-authored vs hand-curated: when each makes sense

Hand-curated skills (the ones this repo might ship) are useful as **seeds**:

- A skill that captures organization-specific conventions the agent couldn't infer.
- A skill encoding a security policy ("always check X before doing Y").
- A skill encoding a hard-won workflow that the agent wouldn't discover on its own in
  week one.

But the bulk of the long-term skill set should be agent-authored. Hand-curating skills
past day 30 of usage is a signal you're not letting the loop work.

### The marketplace anti-pattern

OpenClaw users encountered "clawd hub" or similar third-party skill marketplaces — a
place to download community skills. Hermes deliberately avoids this for two reasons:

1. **Security** — third-party skills are an attack vector. The Hermes team has called
   this out as a lesson learned from OpenClaw's CVE history.
2. **Quality** — community skills written by people who don't use your agent will be
   worse than skills the agent writes for itself.

If a community skill is genuinely good, it can go through the Skills Hub curation
process. There's no plan for a free-for-all marketplace.

## Skills Hub vs agent-authored: the high-quality middle ground

The Skills Hub ships with Hermes and contains skills the Hermes team has distilled from
real production usage. The published PR-review skill, for example, is the result of
going through thousands of real PRs and crystallizing the review process down to a
single high-quality skill.

This is the right model for "skills that everyone benefits from": curated by experts,
battle-tested, versioned. If you find yourself writing a hand-curated skill in this repo
that you think _everyone_ would benefit from, consider proposing it upstream to the
Skills Hub instead.

## Discoverability and invocation

A skill becomes useful when the agent finds it. Skills are surfaced via:

- **Automatic loading** when conditions match the skill's "when to use" trigger
- **Explicit invocation** via `/<skill-name>` slash command (CLI or messaging)
- **Listing** via `/skills` (browse all available)

The matching is fuzzy / semantic — the agent doesn't need an exact keyword match. A
skill named `headless-vpn-setup` will trigger when the user asks about VPN setup,
headless server config, or remote network access.

## How this repo should treat skills

Given all the above, `hermes-config/skills/` should be:

- **Tiny** — a small set of seeds, not a marketplace.
- **Curated** — each skill earns its place. The bar is high.
- **Generic enough to be safe** — no PII, no fleet specifics, no organization-specific
  assumptions.
- **Marked as starters** — make it explicit in each skill's frontmatter that the agent
  should adapt or replace it as it learns.

If we ship more than a dozen skills here, we are probably doing it wrong.

## Open questions

- The exact thresholds the Curator uses for active/stale/archive transitions — these are
  tuned over time and may not be in any single doc. Worth reading the source at
  `~/.hermes/hermes-agent/` (look for `curator` references) if you want certainty.
- Whether `/skills` should support categories or just a flat list — depends on how large
  your set grows.
- How to track skill provenance (which interactions birthed which skill) — useful for
  audit, not currently documented.

## References

- [hermes-vs-openclaw.md](hermes-vs-openclaw.md) — philosophical contrast (marketplace
  vs garden)
- [hermes-architecture.md](hermes-architecture.md) — where the skill subsystem fits
  structurally
- [networkchuck-notes.md](networkchuck-notes.md) — concrete examples of skills the agent
  created on its own from the demo
- Hermes docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- Hermes source: `~/.hermes/hermes-agent/tools/skills_hub.py`
