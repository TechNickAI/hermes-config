# Memory providers for Hermes Agent: Honcho vs mem0 vs supermemory

## Conclusion

**Recommendation: Honcho.** It is the only one of the three that reasons over memory
rather than just storing and retrieving facts, it has the deepest Hermes integration (by
an order of magnitude in plugin LOC and test coverage), it is fully self-hostable under
AGPL-3.0 with first-party Docker Compose, and its publicly verifiable benchmarks are
competitive with mem0's while explicitly optimising for token efficiency — the property
that actually moves the needle for an agent that calls memory on every turn.

The runner-up depends on what you optimise for: pick **mem0** if you want the largest
ecosystem and the cleanest fact-extraction API, or **supermemory** if you want the most
polished managed service with the cheapest per-token pricing and don't mind your data
living in someone else's cloud.

> _Opinion, marked as such throughout. Facts are sourced from Hermes plugin code,
> provider docs, and GitHub APIs as of 2026-05-21._

---

## What "memory provider" means in Hermes

Hermes Agent (`NousResearch/hermes-agent`, the open-source autonomous-agent runtime)
treats long-term memory as a **pluggable peer service**. Plugins ship under
`~/.hermes/hermes-agent/plugins/memory/<name>/` and implement the abstract base class
defined in `~/.hermes/hermes-agent/agent/memory_provider.py`:

```text
initialize()           — connect, create resources, warm up
system_prompt_block()  — static text injected into the system prompt
prefetch(query)        — background recall before the turn
sync_turn(u, a, ...)   — mirror writes after each turn
on_session_end(...)    — close-out hooks (summaries, full-session ingest, etc.)
on_delegation(...)     — parent-side observation of subagent work
```

`MemoryManager` enforces a **one-external-provider-at-a-time** rule to prevent tool
schema bloat and conflicting backends. The provider is selected by the `memory.provider`
config key. There are eight memory plugins in-tree today (`byterover`, `hindsight`,
`holographic`, `honcho`, `mem0`, `openviking`, `retaindb`, `supermemory`); this doc
focuses on the three the task asked about.

---

## Side-by-side

| Dimension                      | Honcho                                            | mem0                                        | supermemory                                            |
| ------------------------------ | ------------------------------------------------- | ------------------------------------------- | ------------------------------------------------------ |
| Vendor                         | Plastic Labs (`plastic-labs/honcho`)              | Mem0 Inc. (`mem0ai/mem0`)                   | Supermemory Inc. (`supermemoryai/supermemory`)         |
| What it actually does          | Continual user/AI modelling + dialectic reasoning | LLM-extracted facts + semantic search       | Profile + semantic search + graph + RAG over docs      |
| Stars / forks (2026-05-21)     | 3.8k / 448                                        | 56.3k / 6.4k                                | 22.6k / 2.1k                                           |
| Contributors (approx)          | 31                                                | 313                                         | 76                                                     |
| Open issues                    | 101                                               | 399                                         | 13                                                     |
| Last commit                    | 2026-05-20                                        | 2026-05-20                                  | 2026-05-21                                             |
| License                        | AGPL-3.0                                          | Apache-2.0                                  | MIT (OSS bits) / commercial (managed)                  |
| Primary language               | Python                                            | Python                                      | TypeScript                                             |
| Self-hostable?                 | Yes (first-class Docker Compose)                  | Yes (OSS + self-hosted server)              | Yes (OSS engine; air-gapped option is Enterprise tier) |
| Cloud tier entry price         | Free tier on app.honcho.dev                       | $0 (10k add / 1k retrieve req/month)        | $0 ($5/mo usage included)                              |
| Cloud paid tier                | usage-based (not on public pricing page)          | $19 starter, $79 growth, $249 pro           | $19 Pro, $399 Scale, custom Enterprise                 |
| Pricing model                  | usage-based                                       | flat-tier with request quotas               | token-metered ($0.005/1K SM tokens for memory)         |
| Published benchmarks           | LoCoMo 89.9, LongMem-S 90.4, BEAM 1M 0.618        | LoCoMo 92.5, LongMemEval 94.4, BEAM 1M 64.1 | None public                                            |
| Hermes plugin LOC (Python)     | **4,817**                                         | 373                                         | 791                                                    |
| Hermes plugin test files / LOC | **7 files / ~1,600 LOC (incl. integration)**      | 1 file / 227 LOC                            | 1 file / 411 LOC                                       |
| Hermes plugin maturity surface | dedicated CLI (`hermes honcho ...`), migration    | basic adapter from upstream PR #2933        | container/profile/recall tooling, no CLI               |
| Featured in NetworkChuck video | Yes                                               | No                                          | No                                                     |

LOC numbers are from
`find ~/.hermes/hermes-agent/plugins/memory/<provider> -name '*.py' | xargs wc -l`. Test
LOC totals Honcho's `tests/honcho_plugin/*.py` (6 files) +
`tests/test_honcho_client_config.py`, vs single-file suites for mem0
(`tests/plugins/memory/test_mem0_v2.py`) and supermemory
(`tests/plugins/memory/test_supermemory_provider.py`).

---

## Honcho

**Source:** `~/.hermes/hermes-agent/plugins/memory/honcho/` **Files:** `__init__.py`,
`client.py`, `session.py`, `cli.py`, `plugin.yaml`, `README.md` — plus 7 test files
under `~/.hermes/hermes-agent/tests/honcho_plugin/`.

### What it actually does

Honcho is **not** a fact store. It is a continual user-modelling service. Per the
project tagline, "memory that reasons" — every message triggers a reasoning pass
("Neuromancer", their tuned model) that updates two evolving **representations** per
session:

- **User peer** — the system's evolving model of the user (facts, patterns, conclusions,
  hypotheses).
- **AI peer** — the system's model of the agent's own identity (seeded from a
  `SOUL.md`-style file).

Retrieval has two flavours:

- `session.context(summary=True)` — recent-history summary
- `peer.chat(query)` — **dialectic** endpoint: multi-pass reasoning that returns
  natural-language answers ("what does this user actually want?"), not raw chunks.

The Hermes plugin's `client.py` injects both layers into the **user message** (not the
system prompt, to preserve provider prompt caching), wrapped in `<memory-context>`
fences. Cadences are independently tunable: base context refreshes every
`contextCadence` turns, dialectic supplements fire every `dialecticCadence` turns. Both
are truncated to fit `contextTokens` budget via `_truncate_to_budget`.

### Self-hosting

Yes, first-class.
`git clone https://github.com/plastic-labs/honcho && docker compose up -d --build`. The
compose stack ships Postgres + Redis + the API server. There is no prebuilt Docker Hub
image — you build from source. Embeddings are configurable as of PR #678 (May 2026). A
community one-command installer exists at `elkimek/honcho-self-hosted` with pre-wired
model tiers and Hermes integration.

### Cloud pricing

The public pricing page returned 404 at time of writing; the marketing site directs new
users to `app.honcho.dev` free tier and surfaces usage-based pricing for paid plans
without public per-unit numbers. **Opinion:** the missing pricing page is a small but
real friction for buyers evaluating the cloud tier — but irrelevant if you self-host.

### Recall quality

From the Honcho marketing page (linking to `evals.honcho.dev` for verification):

- LoCoMo: **89.9%**
- LongMem-S: **90.4%**
- BEAM 100K: 0.630, BEAM 500K: 0.646, BEAM 1M: 0.618
- "60-90% token savings" vs full-context approaches (their framing)

Token savings are the differentiator they lean on hardest, and it matches the plugin's
architecture — the dialectic answer is 1–3 paragraphs, not a wall of retrieved chunks.

### Latency

Honcho's recall is **dialectic** — a model call under the hood — so cold-path latency is
higher than a vector-store lookup. The Hermes plugin mitigates this with:

- `_DEFAULT_HTTP_TIMEOUT` cap (configurable via `HONCHO_TIMEOUT`) — the comment in
  `client.py` is explicit: "without a cap the agent can block indefinitely when the
  Honcho backend is unreachable, preventing the gateway from delivering the
  already-generated response."
- An **async writer thread** (the file is laced with "Sentinel to signal the async
  writer thread to shut down" markers) so writes never block the response path.
- `prefetch(query)` runs in the background before the turn, so the dialectic answer is
  usually warm by the time the model needs it.

**Opinion:** This is the right shape. Sync recall on a reasoning service would be a bad
fit; the plugin's deferred-write + prefetched-read architecture earns most of the
quality without the latency.

### Privacy

Cloud tier: data lives on Plastic Labs infrastructure. AGPL means self-hosting is table
stakes — pull the repo, run Postgres locally, your conversations never leave your
network. For an agent with messaging integrations (WhatsApp/iMessage/etc.) this matters
more than it does for a coding assistant.

### Hermes integration maturity

This is where Honcho pulls away. **4,817 LOC** across `__init__.py`, `client.py`,
`session.py`, `cli.py` — vs ~400 for mem0 and ~800 for supermemory. Features that exist
in Honcho but not the others:

- Dedicated CLI: `hermes honcho setup`, `hermes honcho identity <file>`,
  `hermes honcho migrate` for moving native memory files (USER.md, MEMORY.md, SOUL.md,
  etc.) into Honcho peers.
- Two-layer context injection with independent cadences and budget management.
- Dialectic-depth presets and observation-mode booleans.
- 7 test files including async-writer, session, CLI, client config, and
  empty-profile-hint coverage.

### Project health

- 3.8k stars, 31 contributors, last commit yesterday, 101 open issues — small but active
  core team.
- AGPL-3.0 license is intentionally copyleft (forces forks to publish source).
- Recent commits include telemetry events, configurable embeddings, deriver healthcheck
  gating — actively maintained.

### Setup complexity

Highest of the three for self-hosting (Postgres + Redis + build-from-source), trivial
for cloud (`hermes honcho setup`, paste API key). The plugin handles the rest.

---

## mem0

**Source:** `~/.hermes/hermes-agent/plugins/memory/mem0/` **Files:** `__init__.py`
(only), `plugin.yaml`, `README.md`. No standalone client or CLI — calls go through the
upstream `mem0ai` SDK.

### What it actually does

Mem0 is the prototypical "extract facts from conversations and store them" memory layer:

1. After each turn, send the conversation to Mem0's API.
2. A server-side LLM extracts atomic facts.
3. Facts are embedded, deduplicated against existing memories, and stored.
4. On query, semantic search with optional reranking returns matching facts.

The Hermes plugin (per `__init__.py` header) is "Server-side LLM fact extraction,
semantic search with reranking, and automatic deduplication via the Mem0 Platform API."
Tools exposed to the model: `mem0_search`, `mem0_conclude`, `mem0_profile`.

Notable plugin defences:

- Circuit breaker — after N consecutive failures, pauses calls for
  `_BREAKER_COOLDOWN_SECS` to "avoid hammering a down server."
- Background prefetch thread with a 3s join timeout on the read path.

### Self-hosting

Yes — Mem0 OSS has a self-hosted server (`server/` Docker Compose stack) with Postgres +
pgvector + `gpt-4.1-nano` as the default LLM (`MEM0_DEFAULT_LLM_MODEL`). Library
defaults differ: when you `import mem0` directly, it uses local Qdrant at `/tmp/qdrant`
and OpenAI `gpt-5-mini`. Reranker disabled until configured.

### Cloud pricing

Most transparent of the three:

| Plan       | Price   | Add reqs/mo | Retrieve reqs/mo | Projects  |
| ---------- | ------- | ----------- | ---------------- | --------- |
| Hobby      | Free    | 10,000      | 1,000            | 1         |
| Starter    | $19/mo  | 50,000      | 5,000            | 1         |
| Growth     | $79/mo  | 200,000     | 20,000           | 3         |
| Pro        | $249/mo | 500,000     | 50,000           | unlimited |
| Enterprise | custom  | unlimited   | unlimited        | unlimited |

On-prem, audit logs, SSO, custom integrations gated to Enterprise.

### Recall quality

mem0 publishes the **strongest headline numbers** of the three (own benchmarks, take
with appropriate salt):

- LoCoMo: **92.5%** (vs Honcho's 89.9)
- LongMemEval: **94.4%**
- BEAM 1M: **64.1** (vs Honcho's 0.618; scales differ — these are not directly
  comparable across reports)
- BEAM 10M: 48.6
- Mean ~6,800 tokens per retrieval, vs 25,000+ for full-context baselines

**Opinion:** mem0's benchmark page is more polished but harder to verify than Honcho's
open-source `honcho-benchmarks` repo. Treat both as vendor-published.

### Latency

A turn round-trip adds: fact-extraction LLM call (write path, async in the plugin) +
embed + vector search + rerank (read path, sync). Concrete numbers aren't published;
**opinion:** expect 100–500ms added latency for a typical recall on the managed cloud,
significantly more if self-hosting on a small box.

### Privacy

Cloud-first. Data lives on Mem0 infrastructure. Self-host removes that concern entirely
(Apache-2.0 license, no AGPL strings attached). On-prem deployment is Enterprise-tier on
the managed side.

### Hermes integration maturity

**Thinnest of the three plugins** at 373 LOC, single `__init__.py`. The plugin header
says "Original PR #2933 by kartik-mem0, adapted to MemoryProvider ABC" — it was a
contribution from the vendor, then trimmed to the ABC. There is no `client.py`,
`session.py`, or `cli.py`; everything is in the one file via the upstream `mem0ai` SDK.
Tests: one file, 227 LOC.

**Opinion:** Functional and well-defended (circuit breaker, async prefetch), but it
treats mem0 as a commodity recall API — there is no Hermes-specific feature surface the
way Honcho has dialectic cadence knobs.

### Project health

The big number repo of the three: **56k stars, 313 contributors, last commit
yesterday**. Strong velocity, lots of integrations (the recent commits include CLI
`mem0 whoami`, `mem0 agent-rush`, project scoping, identity banner). 399 open issues
reflects the scale.

### Setup complexity

Easiest cloud setup (paste `MEM0_API_KEY`, done). Self-hosting is a Docker Compose
spin-up. The plugin requires OpenAI by default for embeddings (overridable).

---

## supermemory

**Source:** `~/.hermes/hermes-agent/plugins/memory/supermemory/` **Files:**
`__init__.py`, `plugin.yaml`, `README.md`. Like mem0, a single Python module; the heavy
lifting goes through the upstream `supermemory` SDK and direct calls to
`https://api.supermemory.ai/v4/conversations`.

### What it actually does

Supermemory positions itself as **context infrastructure** rather than just memory. Per
its pricing page, it sells four products together:

- **Memory** — memory graph per user, auto profiles and fact hierarchies.
- **SuperRAG** — multimodal extraction + contextual chunking + retrieval ("no embeddings
  or vectors required" — they use their own model).
- **Search & Traversal** — semantic search + graph traversal, sub-300ms p50 claimed.
- **Operations** — connectors (Google Drive, Notion, Gmail, GitHub, S3, web crawler).

The Hermes plugin exposes: `supermemory_store`, `supermemory_search`,
`supermemory_forget`, `supermemory_profile`. On each turn it can prefetch a profile +
search context, capture cleaned conversation turns, and ingest the full session on
session-end.

Notable plugin behaviour:

- `_TRIVIAL_RE` regex filters out "ok / thanks / yes / no" turns from capture.
- Container/profile-scoped storage with optional multi-container mode.
- Strips `<supermemory-context>` and `<supermemory-containers>` fences from prior turns
  to avoid context pollution.

### Self-hosting

The OSS engine is MIT licensed on GitHub (22.6k stars, TypeScript). **Air-gapped
self-hosting is explicitly an Enterprise-tier feature** on the managed pricing page,
which is doublespeak common to managed-OSS products: the code is open, but the turnkey
deployment story is sold. Realistically self-hostable if you're willing to operate it
yourself.

### Cloud pricing

Token-metered, lowest unit cost of the three:

| Plan       | Price   | Notes                                                                  |
| ---------- | ------- | ---------------------------------------------------------------------- |
| Free       | $0      | $5/mo usage included                                                   |
| Pro        | $19/mo  | ~$20 usage included, 2 teammates, connectors                           |
| Scale      | $399/mo | ~$600 usage included, all connectors, SOC 2, HIPAA, self-hosted option |
| Enterprise | custom  | air-gapped, dedicated infra, SLA                                       |

Per-unit: **$0.005/1K SM tokens for memory** (plain text), $0.010 for rich content;
SuperRAG $0.001/1K tokens; search $0.005/1K queries. Per the marketing copy: "2× cheaper
than next-best, with better quality. Powered by our own model."

### Recall quality

**No public benchmark numbers.** The marketing claims "sub-300ms p50" for search and
"better quality" than competitors but does not publish LoCoMo / LongMemEval / BEAM
scores. **Opinion:** in a market where Mem0 and Honcho both publish numbers, the absence
is conspicuous.

### Latency

Sub-300ms p50 search latency claimed. The plugin uses a `_DEFAULT_API_TIMEOUT = 5.0`
seconds and `_DEFAULT_MAX_RECALL_RESULTS = 10`. Prefetch runs synchronously on the read
path in the current implementation.

### Privacy

Cloud-first. Air-gapped self-hosting and HIPAA BAA gated to the Scale ($399/mo) and
Enterprise tiers. SOC 2 and HIPAA compliance advertised.

### Hermes integration maturity

**Middle of the pack** at 791 LOC and 411 LOC of tests. The plugin is well-written — it
has profile-scoped containers, multi-container mode, capture-mode tuning, search- mode
tuning (hybrid/memories/documents), and explicit forget tooling. But there's no
dedicated CLI, no migration path from other memory backends, no equivalent of Honcho's
dialectic cadence concept.

### Project health

22.6k stars, 76 contributors, last commit today, **only 13 open issues** (notable —
either very responsive or aggressive triage). Recent commits are heavily product/UX
focused (settings redesign, billing tabs, dashboard fixes) which signals the team's
priority is the managed product.

### Setup complexity

Easiest cloud setup of the three (Hermes plugin is referenced directly on the
supermemory pricing page — "Hermes Plugin" listed as a Free-tier inclusion). Setup is
`pip install supermemory`, paste API key.

---

## How they map to Hermes's architecture

| Need                                             | Honcho      | mem0       | supermemory   |
| ------------------------------------------------ | ----------- | ---------- | ------------- |
| Cross-session user modelling                     | **Native**  | Derived    | Derived       |
| AI peer / agent identity                         | **Native**  | No         | No            |
| Multi-document RAG (PDFs, websites, transcripts) | No          | Limited    | **Native**    |
| Self-hosted, fully air-gapped, free              | **Yes**     | Yes        | Yes (DIY)     |
| Lowest per-call cost on managed cloud            | usage-based | tiered req | **per-token** |
| Plugin tooling depth (CLI, migration, presets)   | **Heavy**   | Light      | Medium        |
| Tested under load in the Hermes test suite       | **7 files** | 1 file     | 1 file        |

---

## Why Honcho wins for this repo (opinion)

For a messaging-channel agent that reads and writes memory on **every** turn:

1. **Hermes integration is not equal across providers.** The Hermes plugin for Honcho is
   12× the size of mem0's and 6× the size of supermemory's, with a real CLI and 7 test
   files. That's not marketing — that's where the maintainers chose to spend their time.
2. **The dialectic model fits an agent better than a fact store.** When the agent asks
   "what does this person actually want?" before composing a reply, a 2-sentence
   reasoned answer beats a list of 10 retrieved fact chunks at the same token cost.
3. **Token efficiency compounds.** 60–90% claimed token savings on the recall path,
   multiplied across millions of turns over an agent's lifetime, is meaningful —
   especially for any maintainer running their own inference budget.
4. **AGPL + first-class Docker Compose** keeps data sovereignty on the table without
   degrading the feature surface (cf. supermemory, where air-gapping is gated to
   $399+/mo). Sensitive personal context handling matters more here than for a
   coding-only assistant.
5. **NetworkChuck's Hermes video specifically features Honcho** — the public integration
   story is built around it, which means setup paths and docs are battle-tested for the
   most common Hermes-user flow.

The honest tradeoffs:

- Honcho's benchmark scores are **slightly lower** than mem0's headline numbers.
- Honcho's managed cloud doesn't have a public pricing page.
- Honcho's plugin LOC is higher partly because the feature is more complex — that's more
  surface area to break, not just more capability.

If you can live with those, recommend Honcho. If you want vanilla "remember user
preferences" and the largest community, mem0 is a reasonable second choice. If you want
a polished managed product with the cheapest per-token cost and you're storing documents
alongside conversations, supermemory.

---

## Sources

- `~/.hermes/hermes-agent/agent/memory_provider.py` — the ABC
- `~/.hermes/hermes-agent/plugins/memory/honcho/` — Honcho plugin (client.py,
  session.py, cli.py)
- `~/.hermes/hermes-agent/plugins/memory/mem0/` — mem0 plugin
- `~/.hermes/hermes-agent/plugins/memory/supermemory/` — supermemory plugin
- `~/.hermes/hermes-agent/tests/honcho_plugin/`, `tests/plugins/memory/test_mem0_v2.py`,
  `tests/plugins/memory/test_supermemory_provider.py` — test suites
- https://honcho.dev, https://docs.honcho.dev/v2/documentation/introduction/overview
- https://mem0.ai/pricing, https://mem0.ai/research
- https://supermemory.ai/pricing
- GitHub API (`/repos/plastic-labs/honcho`, `/repos/mem0ai/mem0`,
  `/repos/supermemoryai/supermemory`) — stars, contributors, commit history, retrieved
  2026-05-21
- NetworkChuck's public YouTube walkthrough of Hermes Agent (featuring Honcho)
