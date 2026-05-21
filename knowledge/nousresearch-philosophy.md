# NousResearch Philosophy — "Get Out of the Model's Way"

> Conclusion first: NousResearch's design ethos is **trust the model and shrink the
> harness**. Every Hermes design choice — hard memory caps, agent-authored skills,
> curated rather than open skill hub, fewer messaging platforms than OpenClaw — flows
> from one belief: the model is smart enough; the harness just needs to give it haptic
> feedback to the world. This shapes what `hermes-config` should and should not ship.

Sources for this doc: the NetworkChuck "switching from OpenClaw to Hermes" video
interview with co-founder Jeffrey Canel (transcript indexed locally), the Hermes
architecture itself (which is the more reliable expression of the philosophy), and the
NousResearch site framing.

## The core belief

From Jeffrey Canel in the NetworkChuck interview:

> We are at a point now where the AI models we're getting, they're good. Like, if we
> stopped getting new models at this point, I think we would be at a point where we
> could have AGI. What matters the most now is using the right harness, tweaking the
> tools around it to make it awesome.

And:

> Get out of the way of the models. They're smart enough, if we let them, to just figure
> out what it is that you want to do. The model is the brain. We just needed to give it
> the hands, the feet, the fingers to touch the world in an appropriate way. The harness
> is the haptic feedback to the model of the world.

This is not "the model needs more scaffolding to be useful." It's "the model is fine —
most of what looks like agent improvement is just letting the model do what it's already
capable of."

## How the philosophy shows up in Hermes' design

### 1. Hard memory caps instead of unlimited memory

`user.md` is capped at 1375 characters. `memory.md` at 2200. Why such tight limits when
you could let them grow?

Because forcing the agent to **delete in order to add** is what keeps the memory sharp.
An unbounded memory file becomes noise; a tight one stays signal. The constraint does
the curation work that an undisciplined user would have to do by hand (and wouldn't).

This is the opposite of the OpenClaw approach, which gave you the room to bloat your
`MEMORY.md` and trusted you to keep it lean.

### 2. Agent-authored skills instead of a marketplace

Hermes doesn't have an open community skill marketplace. The reasons are explicit:

- Security — community skills are an attack vector (OpenClaw's CVE history is the
  cautionary tale).
- Quality — community skills written by people who don't use your agent will be worse
  than skills the agent writes for itself.

Instead, the agent writes its own skills as it learns. The Hermes team curates a
high-quality Skills Hub (e.g. their internal PR review skill, distilled from real
production usage). That's it.

### 3. Fewer messaging platforms than OpenClaw

OpenClaw eventually supported Telegram, Discord, Slack, WhatsApp, iMessage, Signal,
email — every channel under the sun. Hermes deliberately ships fewer with first-class
support.

Jeffrey Canel on this:

> Instead of supporting everything, [we] rather make the experience great with a few
> options.

The implication for `hermes-config`: don't ship a plugin for every channel under the
sun. If Hermes' built-in covers it well, don't recreate it. If a channel isn't
supported, ask whether it's worth running OpenClaw alongside Hermes for that one channel
rather than building a half-quality plugin here.

### 4. Curator that runs during the session, not just at compact

OpenClaw's memory curation ran at conversation compact or new session boundaries. Hermes
runs it **during** the session, around every 10 turns. That's the difference between
"tidy when you remember" and "tidy as you go" — and it's why Hermes feels sharp on day
30 where OpenClaw feels clunky.

### 5. The "feels like a product, not a project" stance

OpenClaw felt like something you maintained. Hermes feels like something you use. This
is intentional — the Hermes team has framed it as the difference between an AI agent
harness built by engineers who use their own product vs. one that accumulates features
faster than it tightens them.

This shows up in small things: better error messages, cleaner one-line install, a setup
wizard that asks the right questions, fewer "you must understand this internal" docs.

## What this means for `hermes-config`

The philosophy is contagious. If we ship a config repo that violates it, we're
undermining the very thing that makes Hermes good. Some implications:

### Ship less, not more

OpenClaw had ~25 skills and ~10 workflows in the config repo. Hermes' philosophy says:
most of those should be replaced by the self-improvement loop or by Hermes' built-ins.
The number of artifacts we ship here should be **a fraction** of what `openclaw-config`
had.

### Trust the user to grow their own

The first temptation when migrating from OpenClaw is to bring everything over. The
philosophy says: don't. Let the user start with a SOUL.md and a memory provider
configured, then **let Hermes shape itself around them**.

### Curate ruthlessly, accumulate carefully

Every piece of content we add to this repo should pass the test: "would removing this
prevent the agent from working well?" If no, don't add it. The Hermes team applies this
internally; we should too.

### Be opinionated about what to drop

When users ask "how do I port my OpenClaw workflow X to Hermes?", the most helpful
answer is often "you don't — Hermes does it via cron + a skill, and the result is
better." This repo should be willing to give that answer instead of porting everything
for compatibility.

### Don't ship a marketplace

We should not become "the place to download Hermes skills." That's anti-pattern by
design. If a skill seems generally useful, propose it to the Hermes Skills Hub instead.

## The deeper "why" — democratic AI

NousResearch frames their mission as:

> A group of researchers who found each other because we care about making humanistic,
> censorship-free, and democratic AI.

(From Jeffrey Canel in the interview.)

The harness reflects this. "Get out of the model's way" is partly a technical
observation about model quality, but it's also a political stance: build infrastructure
that lets the model do what users need it to do, not infrastructure that adds gates,
filters, or "we know better" scaffolding.

For `hermes-config`, that translates to: when in doubt between "make a decision for the
user" and "let the user decide", err toward letting the user decide. The whole point of
running your own AI agent is autonomy; the config shouldn't take that away.

## The "AI to make a better version of you" frame

Also from the interview:

> AI is not meant to replace you. It's meant to make you be a better version of you
> every day.

This is the user-facing version of the philosophy. The agent is a peer that learns about
you, helps you, and grows with you. The Curator + memory caps + self-improvement loop +
Honcho-style peer cards all serve this frame.

For `hermes-config`: when we write user-facing docs, this is the tone. Not "here's how
to configure your tool" but "here's how to set up a peer that'll grow into being
useful."

## How to apply this when you're stuck

If you're writing something for this repo and you're not sure if it fits, ask:

1. **Is Hermes already doing this?** If yes, drop. (Don't add a memory tier system;
   Hermes has one.)
2. **Will the agent learn this on its own?** If yes, don't pre-encode it. (Don't ship
   the workflow the agent will figure out by day 14.)
3. **Am I taking a decision away from the user?** If yes, push back to the user. (Don't
   pick a memory provider for them; give them the comparison and let them choose.)
4. **Will this artifact drift fast?** If yes, prefer a pointer to live docs over a
   snapshot here.

If you can answer "no" to all four, the artifact probably earns its place.

## References

- NetworkChuck interview with Jeffrey Canel, "I'm switching to Hermes (goodbye
  OpenClaw!!)" — distilled in [networkchuck-notes.md](networkchuck-notes.md)
- NousResearch site: https://nousresearch.com
- [hermes-vs-openclaw.md](hermes-vs-openclaw.md) — how this philosophy contrasts with
  OpenClaw's
- [hermes-architecture.md](hermes-architecture.md) — how the philosophy is encoded in
  the architecture
- [skill-system-deep-dive.md](skill-system-deep-dive.md) — the skill subsystem as the
  clearest single expression of the philosophy
