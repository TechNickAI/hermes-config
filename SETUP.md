# Setting this up with your coding agent

You almost certainly arrived here with an agent already running — Claude Code, Cursor,
Codex, or Hermes itself. This page is written for that agent.

**Copy the prompt below and paste it into your agent.** It tells the agent how to read
this repo, how to choose what fits *your* setup, and — importantly — what to leave
alone. It is deliberately conservative: it installs the things that work with zero
configuration, and it *asks* before anything that needs a credential or a running
service.

---

## The prompt

````text
You are setting up my Hermes agent using the hermes-config starter kit.

REPO: https://github.com/TechNickAI/hermes-config
It is NOT installable and has no setup command. It is a library of config files I copy
into ~/.hermes/. Take what fits, leave the rest. Do not fork it, do not vendor it, and
do not "install the whole thing" — most of it will not apply to me.

STEP 1 — Read the machine-readable index FIRST, not the README.
  skills/MANIFEST.yaml lists every skill with four fields that matter to you:
    scope                 solo | fleet | migration
    requires              external deps that must exist BEFORE the skill works
    works_out_of_the_box  true = copy it and it runs
    use_when              the trigger conditions for the skill
  Read that file once instead of opening nineteen SKILL.md files.

STEP 2 — Work out what I actually am, then filter.
  Ask me these three questions ONLY if you cannot infer the answers from my machine:
    a) Is this one agent on one machine, or several agents across hosts?
       -> If one machine: install scope=solo only. SKIP everything scope=fleet.
          Those assume multiple hosts, a cron fleet, Caddy/PM2, or a self-hosted
          LLM router. On a single laptop they are dead weight at best and
          confusing at worst.
    b) Am I migrating from OpenClaw?
       -> If no: SKIP scope=migration entirely.
    c) What do I actually want the agent to be better at?
       -> Match against `use_when`. Do not install a skill because it sounds
          impressive. An unused skill is a small permanent tax on the agent's
          attention.

STEP 3 — Install the zero-friction set first, and stop there for now.
  Everything with works_out_of_the_box: true and scope: solo is safe to copy with no
  configuration. Start with these three unless I said otherwise:
    recall          — restores context after /new. The one everyone keeps.
    multi-review    — reviews your own work through several lenses before you ship it.
    trust-framework — teaches you when to act autonomously and when to ask me.
  Copy with:  cp -r skills/<name> ~/.hermes/skills/
  Create ~/.hermes/skills/ first if it does not exist.

STEP 4 — Give me a personality, and let me choose it.
  templates/soul/ has four SOUL.md presets: personal-assistant, engineer, it-admin,
  family-companion. ~/.hermes/SOUL.md is the highest-leverage file in the whole setup —
  it is loaded into every single conversation.
  Show me the four one-line descriptions and let me pick. Do NOT pick for me.
  If ~/.hermes/SOUL.md already exists, STOP and show me a diff before touching it.
  Overwriting an existing SOUL.md destroys personality I may have spent months tuning.

STEP 5 — Anything with a `requires` entry: ASK, do not install.
  For each skill I might want that has requirements, tell me exactly what it needs
  (an API key, a `gog auth login`, a running Caddy) and let me decide. Do not copy a
  skill whose dependency is missing and then report success — it will fail silently
  the first time I actually use it. Verify the dependency exists first:
    command -v gog          # google-docs / google-sheets / google-slides
    command -v gh           # address-pr-comments, pr-review-sweep
    test -n "$XAI_API_KEY"  # grok-search
  If the check fails, tell me the exact command to fix it and move on.

STEP 6 — The memory plugin is a bigger decision. Explain the tradeoff, then ask.
  plugins/memory/cortex/ is a markdown-backed memory provider with hybrid retrieval.
  It replaces the default memory backend, so it is not a small additive change.
  Read knowledge/memory-providers.md and give me the honest tradeoff in three
  sentences before I decide. If I say yes:
    cp -r plugins/memory/cortex ~/.hermes/plugins/
    then set  memory.provider: cortex  in ~/.hermes/config.yaml
  Back up config.yaml before editing it.

STEP 7 — Verify, and be honest about what you could not confirm.
  Run scripts/verify_setup.sh from the repo, or check by hand:
    test -f ~/.hermes/SOUL.md && head -3 ~/.hermes/SOUL.md
    ls ~/.hermes/skills/
    grep -n "provider" ~/.hermes/config.yaml
  Then report:
    - what you installed and why it suits my setup specifically
    - what you SKIPPED and the reason (wrong scope, missing dependency)
    - anything you could not verify
  Do not tell me it works if you have not checked. If something needs my input to
  finish, say exactly what and stop.

RULES THAT OVERRIDE EVERYTHING ABOVE:
  - Never overwrite an existing ~/.hermes/SOUL.md, config.yaml, or memories/ without
    showing me a diff and getting a yes. These hold accumulated personal state.
  - Copy, do not symlink into ~/.hermes/, unless I ask for the tracked setup. Skills
    are seeds; Hermes will edit them as it learns, and a symlink turns that into an
    accidental commit against this repo.
  - Fewer, well-chosen skills beat a full sweep. If you are unsure whether I need
    something, leave it out and mention it.
````

---

## If you'd rather do it yourself

The prompt above is just an encoding of these rules:

1. **Read `skills/MANIFEST.yaml`**, not nineteen skill files.
2. **Filter by `scope`.** One machine → `solo` only.
3. **Start with `works_out_of_the_box: true`.** Nothing to configure, nothing to debug.
4. **Pick a SOUL preset deliberately** — it shapes every conversation.
5. **Treat anything with `requires` as a decision**, not a default.
6. **Verify**, then be honest about what you couldn't confirm.

## Keeping copies current

Copying is one-way — nothing phones home, and nothing updates itself. That's deliberate,
but it means a skill you copied months ago can silently fall behind.

```bash
# from a fresh clone of this repo
python scripts/check_updates.py      # compares ~/.hermes/skills versions against the repo
```

It only reports; it never overwrites. Hermes rewrites its own skills as it learns, so
your local copy having *diverged* is usually a feature — this just tells you where you
stand.
