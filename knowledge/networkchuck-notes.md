# NetworkChuck — "Switching from OpenClaw to Hermes" (distilled notes)

> Video:
> ["I'm switching to Hermes (goodbye OpenClaw!!)"](https://www.youtube.com/watch?v=QQEgIo4Juxg)
> by NetworkChuck. Includes an interview with Jeffrey Canel, NousResearch co-founder.

This doc preserves the substance of the video so future contributors don't have to
rewatch. Direct quotes from Jeffrey Canel are marked. Editorial framing is mine.

## The five reasons NetworkChuck switched

NetworkChuck builds an IT-administration agent named "Ron Weasley" live during the
video. Through that build, he organizes his case for Hermes into five reasons:

### 1. Vibe / mission / branding

Aesthetic and identity matter to the agent experience. The Hermes website, branding, and
overall feel signal "this is a project with taste, by people who care." For a tool
you'll spend hours interacting with, that's not cosmetic — it's part of why you trust
it.

> "As much as we're online, you and me are like terminally online, we're also like
> creatures of this world. And our site and our taste and all of that are part of who we
> are. We put we spent a lot of time to make sure that we have that vibe feel behind
> it." — Jeffrey Canel

### 2. Memory

The headline reason most users will stick around. Hermes' memory system has three
differences from OpenClaw that compound:

1. **Hard char limits** on `user.md` (1375) and `memory.md` (2200). Forces curation;
   prevents drift.
2. **Background fact-checking around every 10 turns** — the agent prunes/updates memory
   during the session, not just at compact.
3. **Pluggable memory providers** like Honcho that build a peer card / personality
   profile in the background.

NetworkChuck's quote on the day-30 difference:

> "This is why OpenClaw feels clunky and bloated on day 30 versus Hermes."

His Honcho moment: walking the streets of Tokyo, having early-morning conversations with
his agent. Honcho built a "peer card" inferring his personality and habits. One trait it
noted, paraphrased: _"high friction technical procrastination — gravitates toward tool
building, wiring, to avoid high-stakes communication or soul work."_ He said "ouch" on
camera — the system had read him.

See [memory-deep-dive.md](memory-deep-dive.md) for the full memory subsystem
walkthrough.

### 3. The people behind it

NousResearch existed before Hermes-the-agent. They're AI researchers training their own
models (also called Hermes — separate product family, same name).

Jeffrey Canel on origin:

> "Hermes agent started out as an internal tool that we wrote, we started it almost 6 to
> 7 months ago as a tool we were using internally to like prototype this recursive
> self-improvement for model training. And when OpenClaw came out, it was actually kind
> of weird cuz we're like, we actually already have this."

And on their mission:

> "We are a group of researchers who found each other because we care about making
> humanistic, censorship-free, and democratic AI."

And on dogfooding:

> "We have our agents in our Discord channels and have them running and we talk to them
> like their team members basically. There's just a Hermes agent who's like the
> developer, you know, the system admin. So someone comes into our Discord and says,
> 'Oh, something this happened, I got this error message.' We're able to just talk to
> the Hermes agent. And what's really amazing is that through having just done this,
> that agent has built up a huge skill set of skills particularly related to debugging
> our infrastructure."

This is the core dogfood case: the agent literally became their team's debugger by
accumulating skills from real interactions.

### 4. The self-improvement loop (skill system)

Likely the most differentiated single feature. The agent writes its own skills as it
learns. NetworkChuck demonstrates this live: he gives Ron Weasley a Tailscale key, asks
Ron to set up the headless client. After the agent succeeds, it autonomously creates a
skill: "Tailscale client operations". NetworkChuck didn't ask for the skill — Ron wrote
it.

Jeffrey Canel on the design:

> "The skill system is sort of like the heart of it — the ability for it to crystallize
> once it's viewed how you operate, to take learnings and crystallize them down into a
> meaningful chunk that it can then reuse. We modeled it after a crude version of how
> potentially we ourselves work: we struggle through things, when we figure out ways
> that solve hard problems we note that down, and then we iterate on those successes."

The Curator agent moves skills through `active → stale → archive` to prevent skill
bloat. See [skill-system-deep-dive.md](skill-system-deep-dive.md).

Anti-pattern reference (Canel on the OpenClaw skill marketplace problem):

> "I don't know if you remember OpenClaw when it first came out — dude, malware central,
> [skill hub] where you could download a bunch of OpenClaw skills that the community was
> uploading. There was bad stuff in there. OpenClaw had a bunch of CVEs or
> vulnerabilities that would get you hacked. As of this video, Hermes hasn't had
> anything agent-related hit it yet. And I think it's because of this mentality right
> here."

Translation: by avoiding the open community marketplace, Hermes avoids the attack
surface that hit OpenClaw.

### 5. Stability — "feels like a product, not a project"

NetworkChuck's day-30 take after using Hermes for a month:

> "I don't think I've had one issue. Nothing I didn't cause myself. [Hermes] just
> doesn't break. The team behind this, they're AI engineers. And I think that's
> important and their mentality behind this matters. They're not just going to add a
> bunch of features to make it bloated or to compete with somebody else."

The contrast with OpenClaw is implicit: more features ≠ better experience.

## Other moments worth preserving

### The bonus reason — Hermes is fun in the terminal

> "That's one of the things I love about Hermes — they actually do make it fun in the
> terminal, not just the messaging app."

The Ink-based TUI is a real piece of craft. Worth experiencing rather than describing.

### The setup wizard

NetworkChuck walks through `hermes setup` live. Highlights:

- **Inference model**: choose your provider. He uses the OpenAI Codex login (reuses your
  ChatGPT subscription) and notes that Grok and OpenRouter also work. Local via LM
  Studio also works (Qwen recommended).
- **Terminal backend**: starts local; remote is configurable.
- **Messaging**: select platform(s) — he chose Telegram via BotFather.
- **User allowlist**: bot only talks to authorized user IDs (gotten via the
  @user_info_bot on Telegram).
- **Gateway as systemd service**: `hermes` wants to run as a background service so
  messages get delivered when you're not at the terminal.

### The Home Assistant integration

NetworkChuck enables the Home Assistant skill mid-conversation. With Home Assistant's IP
and key, the agent figures out the rest — turns off his "Chuck lamp", then turns it on
as blue, then opens his automatic blinds. Real haptic feedback from the agent into the
physical world.

### The Kanban (multi-agent) board

Hermes has a built-in kanban for delegating multi-agent tasks. Live example: "create a
Pokemon card for NetworkChuck, use my likeness, make it an HTML page, make it look
awesome." He assigns to the default profile, watches the agent work, sees the progress
visible in the kanban.

### The dashboard

`hermes dashboard` opens a browser UI that lets you see skills, plugins, profiles,
achievements (gamified), auxiliary models (one big model for reasoning, smaller models
for delegation), and more.

### Computer use (preview)

Hermes shipped computer-use during NetworkChuck's testing — the agent can control the
desktop. Still preview but moving fast.

## Honcho — the optional peer memory layer

Honcho is one of the memory providers (see [memory-providers.md](memory-providers.md)
for the comparison). NetworkChuck deploys it in the cloud and explains how it works:

> "Every time I send a message, that message is also sent to Honcho. Honcho is a peer
> service — it's not Hermes. It's kind of a plugin that will start to reason over what
> I'm saying, and it will start to build out what's called a peer card. Basically, who's
> Chuck? And it gets to know me. Over time, as more and more messages are sent, it will
> start to make more conclusions and learn."

The key insight: Honcho runs alongside the agent and produces additional context that
goes into the agent's system prompt at relevant moments. This is what produced the "high
friction technical procrastination" insight.

Note: Honcho can also be plugged into OpenClaw, but it's a second-class citizen there.
In Hermes it's first-class.

## Things he flagged as concerns (and his counter)

He raised one explicit concern: "you're relying a lot on the AI model to learn things
and self-correct itself. And should we allow it to do that?"

Canel's response:

> "The models are smart. Get out of the way of the models basically. They're smart
> enough if we let them to just figure out what it is that you want to do."

NetworkChuck's resolution after using Hermes for a month: the concern was theoretical;
in practice the agent's self-curation produced better outcomes than his own would have.

## Quotes for marketing copy (if we ever need them)

- "_The agent grows with you. It's going to be better on day 30 than day one._" —
  NetworkChuck
- "_The vibe, the memory, the fact that it learns._" — NetworkChuck (his three-word
  summary)
- "_The harness is the haptic feedback to the model of the world._" — Jeffrey Canel
- "_AI is not meant to replace you. It's meant to make you be a better version of you
  every day._" — Jeffrey Canel
- "_If we stopped getting new models at this point, I think we would be at a point where
  we could have AGI._" — Jeffrey Canel

## What this video tells us about positioning `hermes-config`

A few signals from watching what NetworkChuck found valuable:

1. **The agent's self-improvement is the moat.** Anything we ship that gets in the way
   of the loop is a step backwards.
2. **Memory is the visible-to-the-user differentiator.** The provider docs (Honcho setup
   especially) deserve a real, high-quality home in this repo.
3. **The setup wizard is the first impression.** This repo shouldn't try to replace it —
   defer to `hermes setup` and document what it produces.
4. **Multi-user / non-engineer-friendly is real.** NetworkChuck mentions a household
   member who uses their own agent profile for non-technical work. The implication: this
   repo should be approachable for non-engineers who'll fork/clone it.
5. **The team's philosophy is contagious.** "Don't add features to compete" — we should
   hold ourselves to the same bar.

## References

- Video: https://www.youtube.com/watch?v=QQEgIo4Juxg
- [nousresearch-philosophy.md](nousresearch-philosophy.md) — the philosophy distilled
  from the same source
- [hermes-vs-openclaw.md](hermes-vs-openclaw.md) — the architectural comparison
- [skill-system-deep-dive.md](skill-system-deep-dive.md) — the skill system the video
  showcases
- [memory-deep-dive.md](memory-deep-dive.md) — the memory system the video showcases
- [memory-providers.md](memory-providers.md) — Honcho specifically
