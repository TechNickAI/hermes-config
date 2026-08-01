---
name: grok-search
description:
  On-demand real-time web and X (Twitter) search via xAI's Grok. Reach for this when the
  user explicitly wants Grok to search, or asks for real-time / breaking /
  X-Twitter-native results that a general web search misses. Not the default web search
  — a named tool you invoke deliberately.
version: 0.1.0
license: MIT
metadata:
  hermes:
    requires:
      - "env: XAI_API_KEY (xAI console)"
    tags: [grok, xai, real-time, twitter, x]
    related_skills: []
    # referenced but not shipped here (Hermes core / another source): xurl
---

# Grok Search

Real-time web + X (Twitter) search powered by xAI's Grok agentic search tools. Grok does
the searching server-side and returns ranked results with citations.

This is a **deliberate, on-demand** capability — it does **not** change your default web
search backend. Use it when Grok's real-time reach or native access to X/Twitter is what
the moment calls for; keep using the normal `web_search` tool for everything else.

## When to load

Load this skill when:

- The user says "use Grok to search", "ask Grok", "search X / Twitter", or names Grok/X
  explicitly for a lookup.
- The request is **real-time / breaking** ("what's happening with X right now", "latest
  reactions to Y") where a general index lags.
- The request is **X/Twitter-native** ("what are people posting about Z", "what's the
  sentiment on X about W", "find the thread where…").

Do **not** reach for this for ordinary web research — the default `web_search` provider
is better for general-purpose, index-backed queries and does not carry Grok's per-call
cost.

## Prerequisites

- `XAI_API_KEY` set in the profile's `~/.hermes/.env` (an xAI developer key from the xAI
  console). The script also accepts `--api-key`, but **prefer the env var** — a key on
  the command line lands in shell history and process listings.
- Python 3 (stdlib only — no packages to install).

If the key is missing, the script exits with a clear JSON error; tell the user a key is
needed and stop, don't fall back silently.

## Usage

The helper is `scripts/grok_search.py` with two subcommands. It prints a JSON object to
stdout: `{query, mode, results: [{title, url, description, source}], citations: [...]}`.

**Always shell-quote the query** — wrap it in single quotes so punctuation, double
quotes, and shell metacharacters in the user's phrasing can't break the command or be
interpreted by the shell. If the query itself contains a single quote, escape it
(`'\''`).

### Web (real-time web search)

```bash
python3 scripts/grok_search.py web '<query>' --limit 5
```

Optional:

- `--recency-days D` — restrict to roughly the last D days (translated to a `from_date`
  of today − D, which is xAI's supported recency mechanism)
- `--allowed-domains a.com,b.com` — only these (max 5)
- `--excluded-domains a.com,b.com` — never these (max 5; mutually exclusive with
  allowed)
- `--model M` — override the Grok model (default is a reasoning model required for
  agentic tools)

### X / Twitter search

```bash
python3 scripts/grok_search.py x '<query>' --limit 5
```

Optional:

- `--handles elonmusk,xai` — only posts from these handles (max 20)
- `--from-date YYYY-MM-DD` / `--to-date YYYY-MM-DD` — date window

## How to present results

- Summarize the top results in your own words; cite the URLs. For X results the URLs are
  `x.com/<handle>/status/...` — attribute to the handle when useful.
- If `results` is empty but `citations` has URLs, output the citations as a simple
  bulleted list and tell the user Grok found sources but didn't return structured
  summaries — don't invent descriptions for them.
- If the script errors (non-zero exit, JSON `error` on stderr), report the error
  plainly. Do not invent results.

## Trust & safety

Grok is an LLM, **not** a search index — it _generates_ the result URLs it returns. That
means:

- A query built from **untrusted input** can in principle steer Grok toward
  attacker-chosen URLs. Validate any URL before fetching it downstream, exactly as you
  would treat any model-generated link.
- Treat the descriptions as Grok's summaries, not verbatim source text. When accuracy
  matters, fetch the cited URL and confirm.

## Cost

xAI bills Grok's built-in search tools per call (a flat per-1k-calls fee on top of token
usage). This is why the skill is on-demand, not the default backend — reach for it when
its real-time / X reach earns the call, not for routine lookups.

## Pitfalls

- **Reasoning model required.** The agentic `web_search` / `x_search` tools need a
  reasoning-capable Grok model. The default (`grok-4.5`, the current flagship) supports
  them. If a non-reasoning model is passed via `--model`, the call may return no tool
  results. Stick with the default unless you know the target model supports agentic
  tools.
- **Not an extractor.** This returns search results (title/url/description), not full
  page content. To read a normal web page, pass the URL to the built-in `web_extract`
  tool. **Exception: `x.com` URLs** — `web_extract` almost always fails on them
  (auth-walls, JS rendering), so rely on Grok's own descriptions for X posts rather than
  trying to extract them.
- **Don't wire it as `web.backend`.** If you want Grok as a profile's default search,
  that's the built-in `xai` backend instead — a different, deliberate choice with the
  same trust caveats. This skill deliberately leaves the default untouched.
