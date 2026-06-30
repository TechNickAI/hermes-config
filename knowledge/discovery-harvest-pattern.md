# Discovery Harvest Pattern (for ecosystem-monitoring agents)

> Conclusion first: if you build a Hermes agent that watches the AI ecosystem and
> surfaces what matters, the most common failure is not a weak scoring rubric. It is a
> harvest layer that **can only find things it already knows the name of.** An allowlist
> of named repos and blogs is the right design for _drift detection_ (did something I
> depend on change?) but it is structurally blind to _discovery_ (what new thing is
> taking off that I have never heard of?). The fix is to add a discovery primitive that
> feeds the same strict filter, not to loosen the filter.

This doc describes a reusable design for the harvest stage of a
`HARVEST → FILTER → SYNTHESIZE → DELIVER` monitoring loop. It is written for anyone
wiring a Hermes cron pipeline that scouts a fast-moving software ecosystem.

## The two jobs of a harvest layer (keep them separate)

| Lane                  | Question it answers                                   | Right source shape                                                                 |
| --------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **Drift / watch**     | Did something I depend on change?                     | Allowlist: named repos (releases, commits), named blogs (RSS), provider changelogs |
| **Discovery / scout** | What new thing is taking off that I do not yet track? | Open-ended signals ranked by human engagement and growth, not by name              |

Most homemade pipelines implement only the first lane and assume it covers both. It does
not. An allowlist has zero recall on anything outside the list, and the highest-value
finds (a tool that went from 2k to 24k stars this month) are by definition not on it
yet. You built a good filter for a pipe that was never connected to the open world.

### The tell

If your single non-allowlist source is "Hacker News front page filtered to AI keywords,"
your discovery lane is fed by one noisy source that mostly surfaces front-page drama,
not exploding dev tools. The strict promotion threshold you wrote for the discovery lane
is starving because almost nothing reaches it.

## Three discovery sources that feed the same strict gate

The point is added recall at the harvest stage. The filter does not get easier. Each of
these emits candidates; the existing rubric still decides what gets promoted.

### 1. GitHub star-velocity (highest leverage, cheapest to add)

Poll the GitHub search API for repos under your topic set, sorted by stars, on a daily
interval. The trick is to measure **velocity, not size**. A 140k-star incumbent is not
news, a repo that gained 1,500 stars since yesterday is.

Implementation notes that matter:

- **Keep a cross-run snapshot** of `{repo: star_count}`. Velocity is `today − last_run`.
  You cannot compute it from a single poll.
- **The first run has no prior data point.** Do not emit every large AI repo on boot
  (that floods the inbox with incumbents). Instead, on the first run only, seed the
  baseline silently and emit just _young_ repos that already accumulated many stars fast
  (recent creation date + high count = de-facto velocity). Real deltas take over from
  the second run on.
- **Cap emissions per run** (e.g. 25), sorted by delta. Harvest stays cheap; the filter
  handles relevance. Without a cap, a topic spike can blow your global per-tick item
  ceiling and crowd out drift signals.
- **Prune the snapshot** of repos not seen in ~30 days so the state file stays bounded.

This source alone catches the "star chart is exploding this month" repos that allowlists
miss by construction.

### 2. Engagement-ranked search as a self-feed

A class of community tools (open-source agent skills exist for this) researches a topic
across Reddit, Hacker News, GitHub, prediction markets, and short-form video, then ranks
results by **human engagement** (upvotes, likes, real-money odds) rather than by an
opaque algorithm. Run one nightly over a small rotating set of queries drawn from your
own open problems (e.g. "agent memory", "context compression", "LLM routing", "agent
skills") and pipe the top hits into the harvest inbox.

This is deliberately eating your own dogfood: the agent's blind spot is "find trending
things by human engagement," so you hand it exactly that primitive instead of hoping the
allowlist stumbles onto it.

### 3. Broader social, the right way

Swap "front-page top stories" for **search-by-points-over-a-window** (the HN Algolia
search API supports `points>N` with a date filter), and add one or two curated niche
sources where dev-tool zeitgeist actually lives rather than the generic front page.
Dev-tool launches trend in narrow communities days before they hit any front page.

## Anti-patterns (do not do these)

- **Do not loosen the rubric to compensate for weak harvest.** That trades a recall
  problem for a precision problem and floods the brief with noise. Fix the pipe, keep
  the gate strict. These are tier-3 engagement signals by nature, exactly what a good
  filter is built to be skeptical of, so the correct move is better sources feeding the
  same bar, not a lower bar.
- **Do not emit raw repo size as a signal.** "Project has 50k stars" is not actionable.
  "Project gained 1,500 stars in 7 days and maps to a problem you have" is.
- **Do not skip the cross-run snapshot.** Without persisted state there is no velocity,
  only a leaderboard of incumbents you already know.
- **Do not fabricate URLs.** Emit the canonical URL the source API returns (`html_url`
  for GitHub). Never interpolate an internal hash into a URL path; it will 404
  downstream and the signal is silently lost.

## How this maps to the loop

Harvest gets wider; everything downstream is unchanged. The filter, the synthesis step,
and the delivery format keep their existing discipline. You are connecting the discovery
lane to the open world, then letting the same strict rubric decide what is worth a
human's attention.

See [hermes-architecture.md](hermes-architecture.md) for where cron-driven pipelines sit
in the overall system, and [skill-system-deep-dive.md](skill-system-deep-dive.md) for
the self-improvement loop that lets an agent crystallize a harvest fix like this into a
reused procedure.
