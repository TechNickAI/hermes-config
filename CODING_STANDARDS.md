# Coding Standards

How code and skills in this repo are written. Implementers read it before writing a
change; reviewers (claude[bot], a human) apply it to every diff. Project context, setup
and git workflow stay in [`AGENTS.md`](AGENTS.md); the PII rule and substitution table
stay in [`CONTRIBUTING.md`](CONTRIBUTING.md) and outrank everything here.

A rule a machine can check belongs in `.pre-commit-config.yaml` or a test, not here.
When review catches the same mechanical mistake twice, add a check rather than a
sentence.

## Code

Project principles (Hermes-native first, lean, skills are seeds, markdown over JSON,
public-safe) are the filters in
[`CONTRIBUTING.md`](CONTRIBUTING.md#what-belongs-here-and-what-doesnt); persistent state
is markdown or SQLite.

- **No `pyproject.toml` at the root.** Plugins use the Hermes plugin manifest format and
  are self-contained.
- **One runtime seam.** `plugins/memory/cortex/hermes_compat.py` is the only module that
  imports the Hermes runtime (`agent`, `tools`, `hermes_cli`, `hermes_constants`). Need
  something new? Add it there with a signature-matched stub.
- **Tests pass from a bare clone.** No test may require `hermes-agent` to be installed.
- **Skill dependencies are declared, not assumed.** A skill that needs a package lists
  it in its frontmatter `metadata.hermes.requires`; `skills/MANIFEST.yaml` is generated
  only by `scripts/generate_manifest.py`, which reads skills, not plugins. A plugin
  documents its dependency in its own README until a plugin mechanism exists.
- **Migration is a first-class concern.** Hermes ships `hermes claw migrate`; this repo
  documents the strategy around it.
- **Knowledge docs lead with the conclusion.** State what to do, then why.

## Skills and other agent-facing docs

A skill is selected from its `(name, description)` pair alone, and its body is read only
after selection. Write both for the agent that will run them.

- **The description is a trigger.** Lead with `Use when <situation>`, then one line of
  what it does. One trigger per distinct case; synonyms for the same case are one
  trigger written twice. Two skills with interchangeable descriptions are a bug even
  when their bodies differ.
- **State the target behaviour.** "Write one-line comments" steers better than "don't
  write long comments", because a prohibition puts the unwanted behaviour in context. A
  prohibition earns its place only as a hard guardrail, paired with the positive target.
- **Every step ends on a checkable completion criterion.** "One command that goes red on
  the user's exact symptom, already run and shown" beats "understand the bug". Vague
  bounds invite stopping early.
- **Inline what every path needs; move the rest to `references/`.** A branch only some
  runs take goes behind a pointer whose wording names when to read it. Keep a concept's
  rule, reason and caveats under one heading.
- **Cut no-ops.** A sentence the model already obeys by default costs tokens and says
  nothing; delete the whole sentence. A word too weak to beat the default ("be
  thorough") gets a stronger word, not more words.
- **One source of truth per rule.** Don't restate what `--help`, a config file or the
  directory layout already says; cache only what the agent cannot find by looking (the
  unwritten convention, the reason, the gotcha).
- **Keep the specificity, drop the identity.** The incident is the value of a lesson;
  demote names, hosts and paths to roles and placeholders per `CONTRIBUTING.md`.

## Attribution

The skills section adapts an MIT-licensed third-party skill (`writing-for-agents`); its
copyright and permission notice is reproduced in [`LICENSE`](LICENSE).
