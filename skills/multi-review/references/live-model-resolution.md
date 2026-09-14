# Live model resolution — keep panel slugs from going stale

Hardcoded model slugs in a panel rot fast. Vendors ship new flagships roughly quarterly
(`gemini-2.5-pro` → `gemini-3.1-pro-preview`, `gpt-chat-latest` → `gpt-5.5`,
`grok-4.x`...). A stale panel keeps "working" but silently runs a weaker, older model —
the worst kind of failure because nothing errors. The durable fix is to resolve the
current flagship per family **live** from the provider's model list at runtime, validate
the pick, cache it, and fall back to a known-good pin only if resolution fails.

## The traps a naive resolver hits

1. **Non-monotonic version naming.** `x-ai/grok-4.20` is the _joke_ 4.2 release, so
   `grok-4.3` is actually newer. Lexical or "highest integer minor" comparison ranks it
   wrong. Compare the first `major.minor` as a **decimal**:
   `float("4.3") > float("4.20")`.
2. **Variant noise.** Each family publishes mini/nano/lite/flash/codex/image/audio/oss/
   search/dated-preview/`-pro` (cost) siblings. Exclude them with a per-family regex so
   the pick lands on the clean flagship, not a cheaper or specialized sibling.
3. **Reasoning models validate as "empty."** Gemini 3.x / Grok / o-series burn tokens on
   hidden reasoning. Validate with a generous `max_tokens` and accept a non-empty
   `reasoning` field, or a working model looks dead and gets wrongly excluded.
4. **Floating aliases drift.** `gpt-chat-latest` / `gemini-pro-latest` can resolve to a
   chat-tuned or stale build. Pin the explicit latest instead.
5. **Resolution must never block the panel.** Any failure (API down, no match,
   validation fail) falls back to the family pin. The review still runs.

## Reference resolver (stdlib only, OpenRouter)

Reads `OPENROUTER_API_KEY` by absolute path (sandboxes rewrite `$HOME`), picks the
highest-decimal-version flagship per family after exclusion, ping-validates it, caches
for 24h, falls back to pins. Adapt `FAMILIES` and `ENV_PATH` to the local profile.

```python
import json, re, time, urllib.request
from pathlib import Path

ENV_PATH = Path('/absolute/path/to/profile/.env')        # adapt
CACHE = Path('/absolute/path/to/profile/model_cache.json')  # adapt
MODELS = 'https://openrouter.ai/api/v1/models'
CHAT = 'https://openrouter.ai/api/v1/chat/completions'
TTL = 24 * 3600

FAMILIES = {
    'Grok':   {'pin': 'x-ai/grok-4.3', 'include': 'x-ai/grok-', 'require': [],
               'exclude': r'(mini|nano|lite|build|code|image|audio|fast|multi-agent|deep-research|:free)'},
    'Gemini': {'pin': 'google/gemini-3.1-pro-preview', 'include': 'google/gemini-', 'require': ['pro'],
               'exclude': r'(flash|lite|image|vision|audio|tts|customtools|preview-\d|:free)'},
    'GPT':    {'pin': 'openai/gpt-5.5', 'include': 'openai/gpt-', 'require': [],
               'exclude': r'(mini|nano|lite|image|audio|oss|codex|chat|search|safeguard|turbo|instruct|-pro\b|gpt-3|gpt-4|:free)'},
}

def load_key():
    for ln in ENV_PATH.read_text().splitlines():
        ln = ln.strip()
        if ln.startswith('OPENROUTER_API_KEY='):
            return ln.split('=', 1)[1].strip().strip('"').strip("'")
    raise RuntimeError('OPENROUTER_API_KEY not found')

def version_score(slug):
    m = re.search(r'(\d+)\.(\d+)', slug)          # decimal: float('4.20') == 4.2
    if m: return float(f'{m.group(1)}.{m.group(2)}')
    m = re.search(r'-(\d+)(?:\b|-)', slug)         # single-int like gpt-5
    return float(m.group(1)) if m else -1.0

def select(fam, ids):
    c = FAMILIES[fam]; ex = re.compile(c['exclude'])
    cands = [s for s in ids if s.startswith(c['include'])
             and all(r in s for r in c['require']) and not ex.search(s)]
    if not cands: return None
    cands.sort(key=lambda s: (-version_score(s), len(s)))  # newest, then clean base name
    return cands[0]

def validate(model, key):
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': 'Reply OK'}],
                       'max_tokens': 3500}).encode()  # reasoning models burn hidden tokens
    req = urllib.request.Request(CHAT, data=body,
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=60))
        m = d['choices'][0]['message']
        return bool((m.get('content') or '').strip() or (m.get('reasoning') or '').strip())
    except Exception:
        return False

def resolve_panel(refresh=False):
    if not refresh and CACHE.exists():
        c = json.loads(CACHE.read_text())
        if time.time() - c.get('at', 0) < TTL:
            return c['panel']
    panel = {}
    try:
        key = load_key()
        req = urllib.request.Request(MODELS, headers={'Authorization': 'Bearer ' + key})
        ids = [m['id'] for m in json.load(urllib.request.urlopen(req, timeout=30))['data']]
    except Exception:
        return {f: c['pin'] for f, c in FAMILIES.items()}   # total fallback
    for fam, cfg in FAMILIES.items():
        pick = select(fam, ids)
        panel[fam] = pick if (pick and pick != cfg['pin'] and validate(pick, key)) else cfg['pin']
    try:
        CACHE.write_text(json.dumps({'at': time.time(), 'panel': panel}))
    except Exception:
        pass
    return panel
```

## Wiring into a panel runner

In the divergence/panel script, call `resolve_panel()` to get the live slugs and demote
the hardcoded list to fallback pins. Print the resolved slugs in the run header so the
panel is auditable ("which models actually ran"). Keep the per-family selection rules in
one place; bump the `pin` only if you want the _offline_ default raised too — the live
path already promotes new flagships on its own.

## Verifying the logic without spending tokens

The selection logic is pure and unit-testable offline:

```python
assert version_score('x-ai/grok-4.3') > version_score('x-ai/grok-4.20')   # 4.3 > 4.2
assert select('Grok', ['x-ai/grok-4.20', 'x-ai/grok-4.3', 'x-ai/grok-4.3-mini']) == 'x-ai/grok-4.3'
assert select('GPT', ['openai/gpt-5.4', 'openai/gpt-5.5', 'openai/gpt-5.5-mini']) == 'openai/gpt-5.5'
assert select('GPT', ['openai/gpt-5.5', 'openai/gpt-6']) == 'openai/gpt-6'   # future auto-promote
```

Run these before relying on the resolver; they catch a bad exclude regex or a version-
parse regression instantly.
