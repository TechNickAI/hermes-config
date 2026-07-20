#!/usr/bin/env python3
"""
panel.py — Mixture-of-Agents proposer layer for hard problems.

Dispatches N model "seats" in parallel. Each seat = (model, backend, role mandate).
Writes every raw response to its own file + a run manifest. No synthesis here — the
orchestrator (the calling agent) does best-of-breed synthesis from the files.

Backends:
  - openrouter : https://openrouter.ai/api/v1  (Grok, Gemini, GPT, open models)
  - omniroute  : a custom OpenAI-compatible router (set OMNI_BASE_URL env var)

Usage:
  python3 panel.py --brief BRIEF.md --seats seats.json --out RUNDIR [--label NAME]

seats.json = list of {"seat","model","backend","mandate"}. mandate is prepended to the brief.
If a seat omits "model", grok is used as the fallback default.

Design notes:
  - stdlib only. Reads keys from the profile .env (override with MOA_ENV_PATH).
  - Each call wrapped so one dead model can't kill the panel.
  - Reasoning models get a high token cap; we accept `reasoning` field if `content` empty.
  - Live model resolution for openrouter flagships; omniroute uses floating aliases.
"""
import json, os, re, sys, time, argparse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

ENV = Path(os.environ.get("MOA_ENV_PATH",
    str(Path.home() / ".hermes/.env")))
OR_BASE = "https://openrouter.ai/api/v1"
OMNI_BASE = os.environ.get("MOA_OMNI_BASE_URL")  # required if using omniroute backend

def load_env(name):
    if not ENV.exists():
        return None
    for ln in ENV.read_text().splitlines():
        ln = ln.strip()
        if ln.startswith(name + "="):
            return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return None

OR_KEY = load_env("OPENROUTER_API_KEY")
OMNI_KEY = load_env("OMNIROUTE_KEY")

# Fallback pins per family (used if live resolution fails)
FAMILIES = {
    "grok":   {"pin": "x-ai/grok-4.3",           "include": "x-ai/grok-",     "require": [],
               "exclude": r"(mini|nano|lite|build|code|image|audio|fast|multi-agent|deep-research|:free)"},
    "gemini": {"pin": "google/gemini-2.5-pro",   "include": "google/gemini-", "require": ["pro"],
               "exclude": r"(flash|lite|image|vision|audio|tts|customtools|preview-\d|:free)"},
    "gpt":    {"pin": "openai/gpt-5.1",           "include": "openai/gpt-",    "require": [],
               "exclude": r"(mini|nano|lite|image|audio|oss|codex|chat|search|safeguard|turbo|instruct|-pro\b|gpt-3|gpt-4|:free)"},
    "deepseek": {"pin": "deepseek/deepseek-chat", "include": "deepseek/deepseek", "require": [],
               "exclude": r"(:free|distill|base)"},
}

def version_score(slug):
    m = re.search(r"(\d+)\.(\d+)", slug)
    if m: return float(f"{m.group(1)}.{m.group(2)}")
    m = re.search(r"-(\d+)(?:\b|-)", slug)
    return float(m.group(1)) if m else -1.0

def resolve_openrouter():
    """Return {family: slug} using live model list, falling back to pins."""
    panel = {f: c["pin"] for f, c in FAMILIES.items()}
    if not OR_KEY:
        return panel
    try:
        req = urllib.request.Request(OR_BASE + "/models",
            headers={"Authorization": "Bearer " + OR_KEY})
        ids = [m["id"] for m in json.load(urllib.request.urlopen(req, timeout=30))["data"]]
    except Exception:
        return panel
    for fam, cfg in FAMILIES.items():
        ex = re.compile(cfg["exclude"])
        cands = [s for s in ids if s.startswith(cfg["include"])
                 and all(r in s for r in cfg["require"]) and not ex.search(s)]
        if cands:
            cands.sort(key=lambda s: (-version_score(s), len(s)))
            panel[fam] = cands[0]
    return panel

def resolve_model_alias(model, fam_default):
    """Resolve a seat's requested model to a live OpenRouter slug.

    Accepts three shapes:
      1. Exact family shorthand ("grok", "gemini", "gpt", "deepseek") -> live-resolved flagship.
      2. A near-miss guess at a family/version ("grok-4-5", "gemini-3-pro-preview",
         "gpt4", "deepseek-r1") -> matched back to the family via prefix/substring and
         resolved to the LIVE flagship (never the caller's stale guessed slug -- the
         caller can't know today's live version, that's the resolver's job).
      3. An explicit full OpenRouter slug containing "/" (e.g. "x-ai/grok-4.3",
         "openrouter/fusion") -> passed through unchanged, it's the caller's own choice.

    Case 2 is the fix for the 2026-07 failure: seats.json used near-miss family names
    that didn't match the old exact-string lookup, so they silently fell through and were
    sent to OpenRouter as bogus literal slugs (400/404). Never silently drop a seat --
    if nothing resolves, fall back to the first family default and print a warning so the
    manifest at least explains what happened instead of a bare API error.
    """
    if model in fam_default:
        return fam_default[model]
    if "/" in model:
        return model  # explicit full slug, caller's own choice
    norm = re.sub(r"[^a-z]", "", model.lower())
    for fam in fam_default:
        if norm.startswith(fam):
            print(f"  [alias] '{model}' -> family '{fam}' -> {fam_default[fam]}", file=sys.stderr)
            return fam_default[fam]
    fallback = list(fam_default.values())[0] if fam_default else "x-ai/grok-4.3"
    print(f"  [alias] WARNING: '{model}' matched no known family, "
          f"falling back to {fallback}", file=sys.stderr)
    return fallback

def call_openrouter(model, prompt, max_tokens=None, temperature=0.8):
    if max_tokens is None:
        max_tokens = 16000 if "fusion" in model else 6000
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": temperature}).encode()
    req = urllib.request.Request(OR_BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    m = d["choices"][0]["message"]
    txt = (m.get("content") or m.get("reasoning") or "").strip()
    usage = d.get("usage", {})
    return txt, usage

def call_omniroute(model, prompt, max_tokens=6000, temperature=0.6):
    if not OMNI_BASE:
        raise RuntimeError("MOA_OMNI_BASE_URL env var is required for omniroute backend")
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": temperature,
                       "stream": False}).encode()
    req = urllib.request.Request(OMNI_BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + (OMNI_KEY or ""), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    m = d["choices"][0]["message"]
    txt = (m.get("content") or m.get("reasoning") or "").strip()
    usage = d.get("usage", {})
    return txt, usage

def run_seat(args):
    seat, model, backend, prompt = args
    t0 = time.time()
    # Fail fast on missing config — don't let the broad except below swallow it
    # into a silent per-seat error with exit code zero.
    if backend == "omniroute" and not OMNI_BASE:
        raise RuntimeError(
            "MOA_OMNI_BASE_URL is unset but an omniroute seat is configured. "
            "Set the env var or remove omniroute seats from the seats file."
        )
    try:
        if backend == "omniroute":
            txt, usage = call_omniroute(model, prompt)
        else:
            txt, usage = call_openrouter(model, prompt)
        err = None
    except Exception as e:
        txt, usage, err = "", {}, repr(e)
    return {"seat": seat, "model": model, "backend": backend,
            "seconds": round(time.time() - t0, 1), "chars": len(txt),
            "usage": usage, "error": err, "text": txt}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--seats", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    brief = Path(args.brief).read_text()
    seats = json.loads(Path(args.seats).read_text())
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)

    orslugs = resolve_openrouter()
    fam_default = {"grok": orslugs["grok"], "gemini": orslugs["gemini"],
                   "gpt": orslugs["gpt"], "deepseek": orslugs["deepseek"]}

    tasks = []
    for s in seats:
        backend = s.get("backend", "openrouter")
        model = s.get("model")
        if backend == "openrouter" and model:
            model = resolve_model_alias(model, fam_default)
        if not model:
            model = list(fam_default.values())[0] if fam_default else "x-ai/grok-4.3"
        mandate = s.get("mandate", "")
        prompt = (mandate + "\n\n" + brief) if mandate else brief
        tasks.append((s["seat"], model, backend, prompt))

    safe_label = re.sub(r'[^A-Za-z0-9_-]', '_', args.label)
    manifest = {"label": args.label, "when": datetime.now().isoformat(),
                "brief_file": str(args.brief), "resolved_openrouter": orslugs,
                "seats": []}
    manifest_path = outdir / f"{safe_label}__manifest.json"

    # Write each seat as soon as it completes. This makes long runs observable and
    # preserves partial value if a slow reasoning model hangs or the parent process
    # times out after useful seats have already returned.
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(tasks)))) as ex:
        future_map = {ex.submit(run_seat, t): t[0] for t in tasks}
        for fut in as_completed(future_map):
            r = fut.result()
            safe_seat = re.sub(r'[^A-Za-z0-9_-]', '_', r['seat'])
            fn = outdir / f"{safe_label}__{safe_seat}.md"
            header = (f"# {r['seat']}  ({r['model']} via {r['backend']})\n"
                      f"_time {r['seconds']}s · {r['chars']} chars · err={r['error']}_\n\n")
            fn.write_text(header + (r["text"] or "(empty)"))
            manifest["seats"].append({k: r[k] for k in
                ("seat","model","backend","seconds","chars","usage","error")} | {"file": str(fn)})
            manifest_path.write_text(json.dumps(manifest, indent=2))

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"PANEL {args.label} -> {outdir}")
    for s in manifest["seats"]:
        print(f"  {s['seat']:22} {s['backend']:10} {str(s['model'])[:34]:34} "
              f"{s['seconds']:6}s {s['chars']:6}c err={s['error']}")

if __name__ == "__main__":
    main()
