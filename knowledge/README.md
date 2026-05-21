# knowledge/

Research, comparisons, and architectural deep-dives that inform what goes (and what does
**not** go) into this repo.

This folder is the **canonical source of truth for design decisions**. Every template,
plugin, or skill that ships in this repo should trace back to a principle or finding in
one of these docs.

## Conventions for adding knowledge docs

- One topic per file. Cross-link liberally with relative paths.
- Lead with the **conclusion** — what should we do, given this knowledge?
- Quote primary sources (Hermes docs, transcripts, source code) when relevant.
- Keep opinions clearly marked — facts at the top, opinions in their own section.
- **Public-safe by default** — see the PII rule in the root `AGENTS.md`. No real names,
  no fleet specifics, no absolute paths under `/Users/<anyone>/`.

## What's _not_ here

- **How-to runbooks** — those live in `docs/`.
- **Templates, plugins, skills** — those are the deliverables, not the knowledge.
- **Per-machine config** — that lives in `CLAUDE.local.md` (gitignored).

## Status

Content lands via grouped PRs. See open issues with the `knowledge` label for what's in
flight.
