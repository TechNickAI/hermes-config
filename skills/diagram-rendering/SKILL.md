---
name: diagram-rendering
description:
  "Use when a fast hosted-render path is needed to turn D2, Mermaid, Graphviz, or
  Chart.js text into an inline-ready PNG for chat (Telegram, Discord, Slack) or a saved
  image. Handles the hosted-render + local-rasterize plumbing and platform gotchas
  (snap-chromium sandbox, D2-is-SVG-only, blank-render detection, transient host
  retries) so any fleet agent gets a clean picture on the first try. Prefer excalidraw
  for hand-drawn whiteboards and architecture-diagram for offline, pixel-controlled dark
  SVG cards; do not send sensitive content to public render hosts."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [diagrams, charts, visualization, telegram, mermaid, d2, graphviz, quickchart]
    related_skills: []
    # referenced but not shipped here (Hermes core / another source): architecture-diagram, excalidraw
---

# Diagram Rendering 📈

Draw a diagram or chart from text and deliver it **inline in chat**. One script,
`scripts/render.py`, covers three diagram languages and data charts, and always ends in
a PNG (or an inline image URL) — because chat platforms render images, not
`.d2`/`.mmd`/`.dot`/`.svg` source.

## Overview

Two jobs, one tool:

- **Diagrams** (flowcharts, architecture, sequence, ER) — you write terse text in
  **D2**, **Mermaid**, or **Graphviz**; the hosted renderer (Kroki) returns an SVG and
  the script rasterizes it locally to a crisp 2× PNG.
- **Data charts** (line, bar, pie, etc.) — you write a **Chart.js** JSON config;
  **QuickChart** renders it, either as a local PNG (default) or a short inline URL.

**Pick the language by the tradeoff you want:**

| Language     | Layout                           | Tradeoff                                                              |
| ------------ | -------------------------------- | --------------------------------------------------------------------- |
| **D2**       | modern, rounded, auto-laid-out   | best-looking default; layout can wander on very dense graphs          |
| **Graphviz** | strict orthogonal, deterministic | most robust auto-layout for dense/dependency graphs; visually stiffer |
| **Mermaid**  | simple flow/sequence             | fastest to write; plainest default styling                            |

**Charts** (QuickChart) cover the data side — P&L curves, win-rate bars, category
breakdowns. Same tool, so one skill draws both a _flow_ and a _plot_.

## When to Use

Use for **fast, auto-laid-out diagrams or charts when hosted rendering is acceptable**
(the content is not sensitive, or you point `*_BASE` at a self-hosted instance).

- "draw / show / diagram this", "make a flowchart / architecture / sequence / ER
  diagram"
- "chart / plot / graph our P&L / win rate / breakdown"

**Route elsewhere:**

- Hand-drawn / whiteboard aesthetic → `excalidraw`.
- Offline, pixel-controlled dark-SVG "card" where you hand-place every box →
  `architecture-diagram`.
- Photographic / illustrative images → an image-generation skill.

## Requirements

- **Headless Chromium** for the diagram paths (rasterizes SVG). The script probes
  `chromium-browser`, `chromium`, `google-chrome`, `google-chrome-stable`, or honors
  `CHROMIUM_BIN`. On macOS set
  `CHROMIUM_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`. **Not
  needed** for `chart --short-url` (pure API call).
- **Network** to `kroki.io` (diagrams) and `quickchart.io` (charts) — free, no key.
  Point at self-hosted instances with `KROKI_BASE` / `QUICKCHART_BASE` (http(s) only;
  these are trusted operator config, never derive them from an untrusted prompt).
- **Pillow** (`pip install pillow`) — recommended; enables real blank-render detection.
  Without it the script only checks PNG magic + a weak size heuristic and cannot
  reliably catch a blank canvas — so **eyeball the image regardless**.

## Workflow

> The script lives next to this `SKILL.md`. Resolve its directory as `SKILL_DIR` and
> call it by absolute path — do **not** assume the caller's working directory:
>
> ```bash
> SKILL_DIR="$(dirname "$(realpath path/to/this/SKILL.md)")"   # or the skill dir you loaded
> ```

### 1. Write the diagram text

Save to a file. Example D2 (`~/flow.d2`):

```d2
direction: down
firehose: "Trades firehose" {shape: oval; style: {fill: "#083344"; stroke: "#22d3ee"; font-color: "#e2e8f0"}}
elig: "Eligible?" {shape: diamond; style: {fill: "#1e293b"; stroke: "#94a3b8"; font-color: "#e2e8f0"}}
ladder: "Price ladder" {style: {fill: "#2e1065"; stroke: "#a78bfa"; font-color: "#e2e8f0"; border-radius: 8}}
firehose -> elig
elig -> ladder: yes
```

Mermaid (`.mmd`) and Graphviz (`.dot`) work the same way — the script passes the text
straight through to Kroki.

### 2. Render to PNG

```bash
python3 "$SKILL_DIR/scripts/render.py" diagram --lang d2       --in ~/flow.d2  --out ~/flow.png
python3 "$SKILL_DIR/scripts/render.py" diagram --lang mermaid  --in ~/flow.mmd --out ~/flow.png
python3 "$SKILL_DIR/scripts/render.py" diagram --lang graphviz --in ~/flow.dot --out ~/flow.png
```

The script prints the absolute PNG path on success and **exits non-zero on a failed or
blank render** (it retries transient host errors first). `--out` must be under `$HOME`
(see pitfall #1). Optional `--width` / `--height` (default 900×700, scaled 2×).

### 3. Deliver inline

The printed path is what the platform renders. Put it in your reply as a **standalone,
unfenced line** (a code-fenced `MEDIA:` will NOT attach the image):

```
MEDIA:/home/you/flow.png
```

Inspect the image first — the blank-check catches _empty_ renders, not _wrong_ content.

### Charts

Write a Chart.js config (`~/pnl.json`):

```json
{
  "type": "line",
  "data": {
    "labels": ["Mon", "Tue", "Wed"],
    "datasets": [
      {
        "label": "P&L $",
        "data": [0, 42, 124],
        "borderColor": "#34d399",
        "backgroundColor": "rgba(52,211,153,0.15)",
        "fill": true,
        "tension": 0.3
      }
    ]
  }
}
```

**Default: local PNG (POSTs the config, so large configs are fine), deliver via
`MEDIA:`:**

```bash
python3 "$SKILL_DIR/scripts/render.py" chart --config ~/pnl.json --out ~/pnl.png
```

**Only if the target channel is known to auto-embed remote image URLs**, a short URL is
lighter (the platform fetches it; no local file):

```bash
python3 "$SKILL_DIR/scripts/render.py" chart --config ~/pnl.json --short-url
#   -> https://quickchart.io/chart/render/zf-...   (a public bearer link)
```

Prefer the **local PNG + `MEDIA:`** default — it renders on every platform and you can
verify the image before sending. Use `--short-url` only for channels that reliably
inline remote images.

## Tiered strategy (what to reach for)

1. **Default diagram → D2.** Best-looking, one command.
2. **Dense / dependency graph, layout getting messy → Graphviz.** Deterministic layout.
3. **Just need a quick flow, styling irrelevant → Mermaid.**
4. **Data, not boxes → chart (QuickChart).** Local PNG default; short-URL only where it
   inlines.
5. **Fully offline / pixel-exact brand card → `architecture-diagram`** (hand SVG).

## Common Pitfalls

1. **Snap Chromium can only WRITE under `$HOME`, and NOT to hidden (dot-prefixed)
   files.** AppArmor silently blocks writes to `/tmp` and to `~/.foo.png` (the
   screenshot just never appears). The script enforces `--out` under `$HOME`
   (symlink-resolved) and uses a _visible_ temp filename for the render. Reading a
   hidden source file is fine; writing a hidden output is not.
2. **Kroki renders D2 to SVG only** — its PNG endpoint 400s for D2. The script always
   fetches SVG and rasterizes locally (for every language, so 2× scale +
   `--width/height` apply uniformly). Don't "optimize" it back to requesting D2 PNG.
3. **Kroki's Mermaid renderer is intermittently flaky** — it occasionally 400s with
   "Failed to launch the browser process" (a failure inside _Kroki's_ own headless
   browser, under load). The script retries transient 5xx/that-400 a couple times. If it
   still fails, it's the host — retry later or switch language.
4. **Untrusted render output is rasterized with JavaScript DISABLED.** Kroki/QuickChart
   responses are outside your trust boundary; SVG can carry scripts. The script renders
   with `--disable-javascript` (Kroki SVG is static and needs none). The `svg`
   subcommand is for **trusted** SVG only.
5. **Blank-render detection catches _empty_ canvases, not _wrong_ content**, and is only
   authoritative with Pillow installed. Valid-but-wrong text renders fine and passes.
   Always eyeball anything that matters for legibility, clipping, and correctness.
6. **Default `Python-urllib` User-Agent gets 403'd by Kroki.** The script sends a real
   UA. If you write your own fetch, set `User-Agent` or you'll get 403.
7. **Both diagram source AND chart data leave the machine** (to Kroki / QuickChart
   respectively). A "private thread" does not protect the data from those providers, and
   a `--short-url` is a public bearer link. For sensitive content, use approved
   self-hosted `KROKI_BASE` / `QUICKCHART_BASE` endpoints or an offline sibling skill
   (`architecture-diagram`, `excalidraw`).
8. **Don't paste a 1000-char raw QuickChart GET URL into chat.** Use the local-PNG
   default or `--short-url`.

## Verification Checklist

- [ ] `--out` path is under `$HOME` and not dot-prefixed
- [ ] Chromium present for diagram paths (`CHROMIUM_BIN` or one of the four probed
      names)
- [ ] Script exited 0 and printed a PNG path (non-zero = blank/failed/host-down after
      retries)
- [ ] **Eyeballed the image** for correctness, clipping, legibility (guard only catches
      _blank_)
- [ ] Delivered as an **unfenced** `MEDIA:<path>` line (PNG) or a `--short-url` link on
      a channel that inlines it
- [ ] Sensitive content? Used a self-hosted `*_BASE` or an offline skill instead
