# SOUL.md starter presets

Hermes loads `~/.hermes/SOUL.md` into every conversation. It sets the agent's
personality, voice, values, and constraints. Done well, it's the difference between "a
tool that responds" and "a peer that gets you".

This folder ships starter SOUL files. Pick one, copy it, edit it.

## How to use

```bash
# Pick a preset
cp templates/soul/personal-assistant.md ~/.hermes/SOUL.md

# Open it and make it yours
$EDITOR ~/.hermes/SOUL.md

# Reload Hermes (or start a new session)
hermes
```

## Available presets

| File                    | Persona                              | Best for                                 |
| ----------------------- | ------------------------------------ | ---------------------------------------- |
| `personal-assistant.md` | Warm, anticipatory, organized        | Daily life — calendar, errands, planning |
| `it-admin.md`           | Crisp, methodical, system-thinker    | Managing servers, fleet, infrastructure  |
| `engineer.md`           | Direct, opinionated, code-first peer | Coding work, architecture decisions      |
| `family-companion.md`   | Patient, plain-language, encouraging | Non-technical household members          |

## Writing your own

A good SOUL.md answers three questions for the agent:

1. **Who are you?** — Voice, tone, defaults
2. **What do you care about?** — Values that shape decisions
3. **Where are the lines?** — Hard constraints (never do X, always do Y)

A SOUL file is not the place for:

- **Knowledge about you** — that's `user.md` (auto-curated, hard-capped at 1375 chars)
- **Knowledge about your environment** — that's `memory.md` (auto-curated, 2200 chars)
- **Procedural how-tos** — those become skills the agent writes itself

The agent will refer back to SOUL when it's unsure how to act. Memory is what; SOUL is
how.

## Length

There's no hard cap on `SOUL.md`, but tight is better than verbose. The presets here aim
for **500-1200 characters** — long enough to set a real personality, short enough to
stay in the system prompt without bloat.

Long SOUL files don't make for better behavior; they make for a more confused agent.
Trust the model.

## Versioning your SOUL

Once you've made your SOUL your own, consider committing it to a private repo (or
`CLAUDE.local.md` if it's tiny). Personalities evolve; you'll want history.

## Related reading

- `knowledge/nousresearch-philosophy.md` — the "get out of the model's way" ethos that
  should infuse your SOUL choices
- `knowledge/hermes-architecture.md` — where SOUL fits in the memory/prompt stack
- [Hermes docs on memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
