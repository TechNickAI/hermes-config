# OmniRoute Fable `think` reviewer

Use when Nick asks for a multi-review that specifically includes the "think" model,
Fable, or OmniRoute.

## Routing

- Provider surface: OmniRoute Anthropic Messages API.
- Endpoint: `https://omniroute.example.com/v1/messages`.
- Model alias to request: `think`.
- Observed actual model in response: `claude-fable-5`.
- Key env var: `OMNIROUTE_KEY`.
- API shape: Anthropic messages, not OpenAI chat completions.

## The `/v1/chat/completions` route DOES work — it just streams by default (corrected one occasion)

Earlier guidance said "do not send `think` to the OpenAI-compatible
`/v1/chat/completions` route, it returns non-JSON or fails." That was a
**misdiagnosis**. The route works fine. The real cause of the "non-JSON" failure:
OmniRoute's `/v1/chat/completions` **streams by default**, returning Server-Sent-Event
chunks (`data: {...}\n\n` lines), so a naive `json.loads(response_body)` throws
`Expecting value: line 1 column 1`.

Fix: send `"stream": false` in the body and you get a single clean JSON object back.
Verified in one case from a <agent-d> script hitting
`https://omniroute.example.com/v1/chat/completions` with model `think`:

```python
body = json.dumps({
    "model": "think",
    "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": USER}],
    "max_tokens": 4000,
    "temperature": 0.4,
    "stream": False,          # <-- without this you get SSE chunks, not JSON
}).encode()
req = urllib.request.Request(
    "https://omniroute.example.com/v1/chat/completions", data=body,
    headers={"Authorization": f"Bearer {OMNIROUTE_KEY}", "Content-Type": "application/json"})
d = json.loads(urllib.request.urlopen(req, timeout=240).read())
text = (d["choices"][0]["message"].get("content")
        or d["choices"][0]["message"].get("reasoning") or "").strip()
```

So there are TWO valid surfaces for `think`:

- OpenAI-compatible `/v1/chat/completions` with `stream:false` (simplest for scripts
  that already speak the OpenAI shape, e.g. the same code that talks to OpenRouter).
- Anthropic `/v1/messages` (below), if you prefer the Anthropic body shape.

`think` is a **floating alias to the latest thinking model** — it resolved to
`claude-fable-5` earlier and to `claude-opus-4-8` on one occasion. That is the point of
using `think` instead of a pinned slug: it always rides the current best thinking model.
Do not hard-code the resolved model name.

## Anthropic `/v1/messages` route (alternative)

Use `/v1/messages` with an Anthropic-style body:

```json
{
  "model": "think",
  "max_tokens": 3500,
  "messages": [{ "role": "user", "content": "...review brief..." }]
}
```

Response content may include `thinking` blocks. For final review synthesis, extract only
blocks where `type == "text"`. Do not surface hidden thinking.

## Good panel pattern

For high-stakes <agent-d> financial strategy reviews, a useful four-panel setup is:

- Fable via OmniRoute `think`
- Grok via OpenRouter `x-ai/grok-4.3`
- Gemini via OpenRouter `google/gemini-pro-latest`
- GPT via OpenRouter `openai/gpt-chat-latest`

Use one shared, self-contained brief and fixed headings so the outputs are comparable.
Synthesize, do not average.

## Verified working one-shot commands (<agent-d> profile, re-verified one occasion)

```bash
# Fable (think)  -- provider is custom:omniroute, NOT custom:omniroute-anthropic
hermes -z "$PROMPT" --provider custom:omniroute -m think --ignore-rules -t ''

# Grok
hermes -z "$PROMPT" --provider openrouter -m x-ai/grok-4.3 --ignore-rules -t ''

# Gemini  -- slug is google/gemini-2.5-pro
hermes -z "$PROMPT" --provider openrouter -m google/gemini-2.5-pro --ignore-rules -t ''

# GPT (OpenAI compat via openrouter)
hermes -z "$PROMPT" --provider openrouter -m openai/gpt-chat-latest --ignore-rules -t ''

# Cheaper/faster: Sonnet via omniroute
hermes -z "$PROMPT" --provider custom:omniroute -m simple --ignore-rules -t ''
```

**Config file that governs these:** `$HERMES_HOME/config.yaml`

### Provider/slug corrections (verified one occasion by direct run)

- **Fable provider alias is `custom:omniroute`** (with `-m think`).
  `custom:omniroute-anthropic` is NOT a valid `hermes -z` provider and fails with
  `AuthError: Unknown provider 'custom:omniroute-anthropic'`. The config has a single
  `omniroute` provider; check with `grep -n "custom:omniroute" config.yaml`.
- **Gemini slug is `google/gemini-2.5-pro`.** This reverses the earlier note: it is
  `google/gemini-pro-latest` that is now stale and returns a 400. Use `gemini-2.5-pro`.

## Running reviewers in parallel

Since `terminal()` blocks on background `&` commands, the cleanest way to parallelize is
`execute_code` (Python subprocess).

**Important: write long/multi-line briefs to a file first.** Passing a multi-line brief
inline via `repr()` or shell quoting fails when the brief contains newlines, special
chars, or is long enough to stress shell arg limits. The safe pattern:

```python
from hermes_tools import terminal, write_file
import concurrent.futures

brief = """...your full review brief..."""

# Write to file first -- prevents shell quoting failures on long/multi-line prompts
write_file("/tmp/review_brief.txt", brief)

reviewers = [
    ("fable",  "--provider custom:omniroute -m think"),
    ("grok",   "--provider openrouter -m x-ai/grok-4.3"),
    ("gemini", "--provider openrouter -m google/gemini-2.5-pro"),
]

def run(name_flags):
    name, flags = name_flags
    result = terminal(f'hermes -z "$(cat /tmp/review_brief.txt)" {flags} --ignore-rules -t \'\'', timeout=120)
    return name, result["output"]

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    results = dict(ex.map(run, reviewers))

for name, text in results.items():
    print(f"=== {name.upper()} ===")
    print(text)
```

For short single-line prompts (under ~200 chars, no special chars), inline is fine:

```bash
hermes -z "What is 2+2?" --provider openrouter -m x-ai/grok-4.3 --ignore-rules -t ''
```

## Root cause of subagent timeouts

`delegate_task` subagents hit `child_timeout_seconds: 600` when given broad I/O tasks
(file crawls, binary downloads, multi-step email searches). For multi-model review,
always use `hermes -z` one-shots -- they're pure text-in/text-out and don't hit the
wall. Only use `delegate_task` for reasoning-heavy subtasks with no external I/O.

The specific failure mode: subagents given tasks like "search all of Dropbox" or "search
Gmail for 2014 emails" will spend 47+ API calls on exploratory I/O before timing out.
Run I/O directly in the parent agent using `terminal()`, then pass the extracted text to
`hermes -z` reviewers as a bounded prompt. Never ask a subagent to do discovery work
without a tight scope and bounded output.

## Fable `think` timeout on long prompts

Fable via `hermes -z... -m think` with a very long brief (~3,700 char) can timeout at
120 seconds (observed once in production). If this happens:

1. Shorten the brief significantly -- cut to core facts + the specific questions.
2. Re-run with the shorter brief. Fable completed successfully on a brief ~1/3 the
   length.
3. Do NOT increase the timeout past 120s -- it's more likely the model is just taking
   longer to reason. A shorter, focused brief produces better output anyway.

## execute_code limitation for hermes -z

`execute_code` (the sandboxed Python runner) can fail with "Could not determine home
directory" when its internal `terminal()` calls invoke hermes processes that need
`HOME`. Use `terminal()` directly from the parent agent for hermes one-shot calls.
`execute_code` is fine for pure Python math and data processing.

## "Could not determine home directory" can also hit parent terminal() calls

The same error can surface intermittently even on plain parent `terminal()` runs of
`hermes -z` / OpenRouter one-shots (observed One case: two consecutive failures, then
success). Fix: prefix the command with `export HOME=/Users/nick` (literal path, the
sandbox rewrites `$HOME`). It cleared immediately on the next run. Treat it as a flaky
env-resolution issue, not a broken provider -- do not abandon the reviewer over it.
