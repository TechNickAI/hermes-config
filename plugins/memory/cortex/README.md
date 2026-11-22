# Cortex Memory Provider

A Hermes
[MemoryProvider plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
backed by a hand-curated markdown knowledge base.

Cortex is a personal knowledge compiler: pages organized into `people/`, `ventures/`,
`topics/`, `decisions/`, `synthesis/`, `learning/`, `research/`, plus a `daily/`
journal. Each page is markdown with YAML frontmatter (title, tags). Unlike server-backed
memory providers (Mem0, Supermemory, Honcho), the KB is a regular filesystem you can
`cd` into, edit by hand, version with git, and read with any tool.

This plugin makes that KB part of the agent loop:

- **Prefetch** before every turn — full-text search over page bodies, top results
  injected into the system prompt
- **Cortex tool** — `search`, `read`, `write`, `list`, `daily` for explicit recall and
  capture
- **Auto-journal** (optional) — append meaningful turns to `daily/YYYY-MM-DD.md`
- **Auto-synthesize** (optional) — drop session trails into `synthesis/` for the
  curation pass

## Requirements

None beyond Hermes core. SQLite is bundled with Python. `pyyaml` is already a Hermes
dependency.

## Storage layout

```
$HERMES_HOME/cortex/
├── people/              # named individuals
├── ventures/            # projects, products, companies
├── topics/              # subject-area knowledge
├── decisions/           # decisions with rationale, dated
├── synthesis/           # cross-cutting summaries, session trails
├── learning/            # how-tos, lessons learned
│   └── archive/         # historical / superseded
├── research/            # external research notes
├── daily/               # YYYY-MM-DD.md journal entries
├── analyses/            # answers filed back via action="file"
├── log.md               # append-only operation log (not indexed)
├── catalog.md           # generated page catalog, rebuilt by lint (not indexed)
└── .plugin.db           # FTS5 index (auto-rebuilt on stale mtime)
```

Page format:

```markdown
---
title: Some Topic
tags: [topic, infra]
updated: 2026-05-24
---

Body markdown here...
```

## Setup

```bash
# Discover and enable
hermes plugins discover
hermes memory setup    # pick 'cortex' from the list
```

Or edit `$HERMES_HOME/config.yaml` directly:

```yaml
memory:
  provider: cortex

plugins:
  cortex:
    store_path: $HERMES_HOME/cortex
    prefetch_limit: 5
    auto_journal: false # append turns to daily/
    auto_synthesize: false # write session trails to synthesis/
```

The store directory and standard subfolders are created automatically on first run. If
you already have a Cortex KB (e.g. from the `cortex` CLI in openclaw-config), just point
`store_path` at it.

## Tool reference

The plugin exposes one tool: `cortex`.

```
cortex(action="search", query="hermes plugin architecture", limit=5)
  → top 5 pages with snippets, sorted by BM25

cortex(action="read", rel_path="topics/hermes-plugins.md")
  → full page body + frontmatter

cortex(action="write", category="topics", title="Hermes Plugins",
       body="...", tags=["hermes", "infra"])
  → creates topics/hermes-plugins.md with YAML frontmatter

cortex(action="list", category="people", limit=20)
  → 20 most recently modified pages in people/

cortex(action="daily", body="Notes about today's work")
  → appends timestamped entry to daily/YYYY-MM-DD.md

cortex(action="scan", terms=["Dana", "Globex"])
  → every live page mentioning each term, with the matching line

cortex(action="supersede", rel_path="people/dana-2025.md",
       superseded_by="people/dana.md", reason="changed jobs")
  → frontmatter + visible banner; drops out of search, stays on disk

cortex(action="file", title="Router comparison", body="...",
       sources=["topics/a.md", "topics/b.md"], query="which router?")
  → analyses/router-comparison.md, with its sources recorded

cortex(action="lint")
  → broken links, orphans, missing pages, stale "currently" claims,
    slug collisions; regenerates catalog.md and logs the pass

cortex(action="log", limit=10)
  → most recent log.md entries
```

## Compiled wiki, not a pile of notes

Cortex follows Karpathy's LLM-Wiki pattern: a source is _compiled into_ the pages it
affects instead of becoming one more page. Ingest looks like this:

1. **scan** the people, projects and concepts the source names.
2. For each affected page decide: new fact (add it), update (rewrite the line in place),
   contradiction (keep both, mark which is current, cite the source), or nothing
   material.
3. **supersede** pages the source makes untrue, pointing at the current one. Never
   delete; history stays readable by path.
4. Answers that pulled several pages together get **file**d back.
5. **lint** periodically; every write op lands in `log.md`.

Temporal validity is plain frontmatter, so humans and other tools can set it:

```yaml
invalid_at: 2026-11-22 # past or today: hidden from default search
superseded_by: people/dana.md # successor; alone, means "invalid since always"
```

A future `invalid_at` is a scheduled expiry; it takes effect on that date without a
reindex because the filter is evaluated at query time.

## Single-provider constraint

Hermes enforces one external memory provider at a time to prevent tool-schema bloat.
Enabling Cortex disables any previously active provider (Honcho, Mem0, Hindsight, etc.)
— they remain installed but inactive. Switch back via `hermes memory setup` or by
editing `memory.provider` in `config.yaml`.

## Companion CLI

The standalone `cortex` CLI (in
[`openclaw-config/skills/cortex`](https://github.com/TechNickAI/openclaw-config/tree/main/skills/cortex))
handles the _compiler_ side of Cortex: scanning external sources, triaging into a review
queue, and rebuilding index files. The plugin handles the _agent_ side: search,
retrieval, and capture during conversation. Both operate on the same store and are
designed to coexist.

Typical workflow:

- Plugin captures durable facts during conversation (`cortex(action='write', ...)`)
- Nightly cron runs `cortex link` / `cortex rebuild-index` to stitch new pages into
  category indexes
- Plugin's FTS5 index auto-rebuilds for any page whose mtime changed

## Why not server-backed?

If you already use Honcho or Mem0 and they work for you, keep them. Cortex is for the
niche where:

- The KB is **yours**, lives on **your disk**, in a format you can read without any tool
  running
- You want hand-curation, not auto-extraction by an external LLM
- You're already keeping markdown notes and want the agent to actually use them
- You want a memory layer you can `git diff`, `grep`, and back up with `rsync`
