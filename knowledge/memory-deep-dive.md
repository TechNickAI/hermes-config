# Memory Deep Dive (architecture, providers, Honcho setup)

> Conclusion first: **Hermes memory is three coexisting layers — hard-capped core
> markdown, an FTS5 session DB, and an optional external provider — and the hard cap on
> the core files is the whole reason it stays sane past day 30.** You don't choose
> between layers; the core is always on, the session DB is always on, and a provider
> plugs in alongside them. Setup is `hermes memory setup` (interactive, no flags), and
> Honcho is the right default if you want one.

This doc is the operational complement to [memory-providers.md](memory-providers.md)
(which vendor and why) and the deeper view into the memory section of
[hermes-architecture.md](hermes-architecture.md). For the OpenClaw → Hermes mapping of
memory concepts, see [paradigm-translation.md](paradigm-translation.md).

## What memory means in Hermes

Hermes splits "memory" into three layers, each with a different job and a different
storage shape. They are additive — turning on a provider does not turn off the core
files; the session DB is on whether you want it or not.

| Layer             | Where                                                        | Shape                                     | Always on?                            | Who writes it                          |
| ----------------- | ------------------------------------------------------------ | ----------------------------------------- | ------------------------------------- | -------------------------------------- |
| Core              | `~/.hermes/memories/memory.md`, `~/.hermes/memories/user.md` | Markdown, hard-capped (2200 / 1375 chars) | Yes                                   | The Curator agent (with file locks)    |
| Session DB        | `~/.hermes/state.db`                                         | SQLite + FTS5 over every session          | Yes                                   | The agent loop, automatically          |
| External provider | API or self-hosted peer service                              | Vendor-specific (graph, vector, etc.)     | No (opt-in via `hermes memory setup`) | The provider plugin, in the background |

The core files load into the system prompt on every turn. The session DB is queried by
the agent when it needs prior-conversation context. The external provider injects a
`system_prompt_block()` and gets `prefetch(query)` / `sync_turn(u, a, ...)` hooks during
the turn — see the abstract base class summary in
[memory-providers.md](memory-providers.md).

The result is that the model gets a small, tight always-on context (core), can search
prior sessions on demand (DB), and — if a provider is configured — gets a curated
external recall layer on top.

## The hard cap is the feature

`memory.md` is hard-capped at **2200 characters**. `user.md` is hard-capped at **1375
characters**. The Curator does not get a warning; the cap is enforced as a budget.

This is not a limitation people work around. It is the whole reason the system stays
healthy. To add a fact, the Curator must **delete** something else. That forces every
write to compete with every existing line on durability, uniqueness, retrievability, and
authority. OpenClaw's `MEMORY.md` had no such pressure and grew to thousands of lines;
Hermes' equivalent never does.

The cap lives in `config.yaml` and can be raised (see config block below), but raising
it defeats the design. The right reflex when the cap feels tight is to wire up an
external provider for the long tail, not to widen the budget.

## The Curator

A background Curator agent fact-checks and prunes `memory.md` and `user.md` roughly
**every 10 turns** (see [hermes-vs-openclaw.md](hermes-vs-openclaw.md)). It runs
_during_ the session, not just at compact or session end, which is why Hermes does not
develop the day-30 bloat OpenClaw was prone to.

Mechanically, the Curator writes through a file lock — `~/.hermes/memories/` contains
`MEMORY.md.lock` and `USER.md.lock` companion files that serialize writes between the
Curator and any other process that might touch them (manual edits, migration tools,
provider plugins reading the current state). This is worth knowing for two reasons:

- If you edit `memory.md` by hand while a session is running, the Curator may overwrite
  you. Stop the agent first.
- If a lock file is left behind after a crash, deleting the stale `.lock` is safe.

## Choosing a provider

`hermes memory status` lists **eight** provider plugins in-tree today. The list is
larger than older docs suggest. Each is either "API key" (managed service), "local"
(self-hosted), or "requires API key" before it can be activated.

| Plugin        | Type                              | Quick characterization                                                                               |
| ------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `honcho`      | API key or self-hosted (AGPL-3.0) | Reasons over memory rather than just storing facts. Deepest Hermes integration. Recommended default. |
| `mem0`        | API key or self-hosted            | Largest ecosystem, clean fact-extraction API. Strong runner-up.                                      |
| `supermemory` | API key (managed)                 | Polished managed service, cheapest per-token. Data lives in their cloud.                             |
| `byterover`   | API key                           | Managed service.                                                                                     |
| `hindsight`   | API key                           | Managed service.                                                                                     |
| `holographic` | Local                             | Self-hosted, no API key needed. Experimental.                                                        |
| `openviking`  | API key                           | Managed service.                                                                                     |
| `retaindb`    | API key                           | Managed service.                                                                                     |

For the deeper comparison of the top three (architecture, benchmarks, pricing,
self-hosting, licensing), see [memory-providers.md](memory-providers.md). The short
version: **start with Honcho** unless you have a reason not to. It is the only one of
the eight that meaningfully reasons over the corpus rather than treating it as a vector
store, and its Hermes plugin is the most mature by an order of magnitude.

`MemoryManager` enforces a **one-external-provider-at-a-time** rule to keep tool schemas
tight and avoid conflicting backends. Switching providers means switching, not layering.

## Setting up Honcho (worked example)

The entire flow is one command — `hermes memory setup` — and it is fully interactive.
There are no flags; the wizard walks every choice.

```text
$ hermes memory setup
? Which memory provider would you like to use?
  > honcho
    mem0
    supermemory
    byterover
    hindsight
    holographic
    openviking
    retaindb

? Honcho requires an API key (HONCHO_API_KEY). Paste it now or set it later: ********

✓ Wrote memory.provider: honcho to ~/.hermes/config.yaml
✓ Wrote HONCHO_API_KEY to ~/.hermes/.env
```

The wizard writes two things:

1. `memory.provider: honcho` into `~/.hermes/config.yaml` (under the top-level `memory:`
   block — see [config block](#config-block) below).
2. `HONCHO_API_KEY=...` into `~/.hermes/.env`.

The env var name pattern is `<PROVIDER>_API_KEY` in upper-case. So `mem0` becomes
`MEM0_API_KEY`, `supermemory` becomes `SUPERMEMORY_API_KEY`, etc.

Verify with `hermes memory status`:

```text
$ hermes memory status

Built-in memory:
  memory.md       enabled  (1834 / 2200 chars)
  user.md         enabled  ( 942 / 1375 chars)

Active provider: honcho

Installed plugins:
  byterover
  hindsight
  holographic
  honcho        ← active
  mem0
  openviking
  retaindb
  supermemory
```

The "← active" marker is the source of truth. If you set a provider in `config.yaml` but
the env var is missing, the status will say so explicitly rather than silently failing
on the first turn.

### Config block

The top-level `memory:` section in `~/.hermes/config.yaml` looks like:

```yaml
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: honcho
```

`memory_enabled` and `user_profile_enabled` control whether the core files load into the
system prompt at all — both default to `true` and should stay that way. The two
`*_char_limit` fields are the hard caps discussed above; raising them defeats the
design. `provider` is the active provider name (matches a plugin name from the table
above), or omit / set to `null` for built-in only.

### Self-hosted Honcho

If you would rather run Honcho locally, the provider supports it via its standard
`HONCHO_BASE_URL` env var pointing at your own deployment. The wizard does not prompt
for this — set it manually in `~/.hermes/.env` after `hermes memory setup`. The Honcho
repo ships a first-party Docker Compose; see [memory-providers.md](memory-providers.md)
for the deployment details.

## Switching providers / turning it off

Three commands cover the lifecycle:

| Command               | Effect                                                                                                                      |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `hermes memory setup` | Interactive wizard. Re-running it switches providers — pick a different one and the wizard rewrites `provider:` and `.env`. |
| `hermes memory off`   | Sets `memory.provider: null`. Core files stay active; the external provider is disconnected. Safe and reversible.           |
| `hermes memory reset` | **Erases** `memory.md` and `user.md`. Destructive — there is no undo unless you have a backup.                              |

`hermes memory reset` is the one to be careful with. It does not touch the external
provider's data (that lives on the provider side); it wipes the core files. Use it when
starting over deliberately, not as part of routine maintenance.

To switch providers cleanly:

1. `hermes memory off` (optional but tidy — closes the current provider)
2. `hermes memory setup` (pick the new one, paste the key)
3. `hermes memory status` (confirm "← active" moved)

There is no migration of recall between providers. Each provider builds its own model of
the user from the conversations it sees post-activation; historical sessions in
`state.db` are still searchable by the agent, but the new provider starts cold and
catches up over the next handful of turns.

## What lives where after setup

After `hermes memory setup` succeeds, this is the file inventory you should expect:

| Path                                   | Purpose                                                |
| -------------------------------------- | ------------------------------------------------------ |
| `~/.hermes/config.yaml`                | `memory:` block updated with `provider:` key           |
| `~/.hermes/.env`                       | `<PROVIDER>_API_KEY=...` added                         |
| `~/.hermes/memories/memory.md`         | Core memory (always present, Curator-managed)          |
| `~/.hermes/memories/user.md`           | User profile (always present, Curator-managed)         |
| `~/.hermes/memories/MEMORY.md.lock`    | Curator lock file (transient, safe to delete if stale) |
| `~/.hermes/memories/USER.md.lock`      | Curator lock file (transient, safe to delete if stale) |
| `~/.hermes/state.db`                   | Session DB (sessions + FTS5 index)                     |
| `~/.hermes/plugins/memory/<provider>/` | The plugin itself (already shipped in-tree)            |

Nothing else needs to be created by hand. The provider's own state (graph nodes,
vectors, summaries) lives either in the managed service or in your self-hosted
deployment — not in `~/.hermes/`.

## Open questions

- **How exactly does the Curator decide what to evict when the cap is reached?** The
  10-turn cadence is documented; the eviction algorithm is not. Worth tracing through
  the source once.
- **What happens to recall across profile switches?** A profile has its own
  `~/.hermes/profiles/<name>/memories/`, but does the provider partition by profile
  automatically, or does it need a per-profile API key? Empirically untested.
- **Self-hosted Honcho with multiple Hermes profiles** — one Honcho instance per
  profile, or one shared with namespace separation? Provider docs are quiet on this.
- **Lock-file recovery** — the locks appear to be advisory, but the exact recovery
  behavior on a crash mid-write has not been stress-tested here.
- **mem0 vs supermemory in-Hermes feel** — the side-by-side in
  [memory-providers.md](memory-providers.md) is on paper; longitudinal feel after a
  month of real use would be useful follow-up research.

## Related reading

- [memory-providers.md](memory-providers.md) — vendor comparison, benchmarks, pricing,
  self-hosting, licensing
- [hermes-architecture.md](hermes-architecture.md) — where memory sits in the broader
  Hermes Python harness
- [hermes-vs-openclaw.md](hermes-vs-openclaw.md) — why the hard-capped core is an
  improvement over OpenClaw's tiered memory
- [paradigm-translation.md](paradigm-translation.md) — per-concept OpenClaw → Hermes
  map, including the memory rows
- [migrator-internals.md](migrator-internals.md) — what `hermes claw migrate` moves into
  `~/.hermes/memories/` and what it does not
