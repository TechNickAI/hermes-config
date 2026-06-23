---
name: google-slides
description:
  "Use when creating, importing, exporting, or quality-checking Google Slides decks via
  markdown-to-PPTX conversion and Drive import."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [google, slides, drive, markdown, pptx, presentation, productivity]
    related_skills: [google-docs, google-sheets]
---

# Google Slides

## Overview

Use this skill when the artifact is a **Google Slides deck**. Slides is the weakest
surface for the local `gog` CLI: `gog slides` only supports create/info/copy/export, and
the local OAuth token did not even include the Slides scope during testing. So content
authoring goes through a conversion or raw-API path, not gog content commands.

Tested-good default: **author Markdown -> pandoc PPTX -> Drive import as native Google
Slides.** In testing this produced a 6-slide deck with correct titles, bullet lists, and
numbered lists, verified by exporting the created deck back to PPTX and reading slide
XML.

## Prerequisites

- **`gog` CLI** (steipete/gogcli) installed and authorized: run `gog auth login` once in
  the operating-system user's real `$HOME`. The Drive import path only needs Drive auth;
  it does not require the Slides API scope.
- **`python3`** on `PATH` (the helper is stdlib-only — no `pip install` required).
- **`pandoc`** installed for the default Markdown -> PPTX conversion
  (`brew install pandoc` on macOS).
- The bundled `scripts/gworkspace.py` reuses gog's stored OAuth refresh token. If lookup
  fails with `{"error": "missing_credentials"}`, see Common Pitfalls for the explicit
  credential-file escape hatch.

> **Run helper commands from the skill directory** so `scripts/gworkspace.py` resolves
> relative to `$PWD`, or use `skills/google-slides/scripts/gworkspace.py` from the repo
> root.

> **$HOME shadowing:** some agent runtimes rewrite `$HOME` to a sandbox path where gog's
> auth doesn't exist. If credential lookup fails in that environment, point `HOME` at
> the real OS user home before invoking the helper.

## When to Use

- User asks for a slide deck, presentation, or pitch.
- User gives an outline or Markdown and wants slides.
- User asks whether to use `gog`, raw API, or browser for Slides.

Do **not** use this for Docs or Sheets; load `google-docs` or `google-sheets`.

## Path A: Markdown -> pptx -> Google Slides (default)

Author the deck as Markdown where each top-level header starts a slide, then convert and
import.

```bash
# 1. Author deck.md (see authoring rules below)
# 2. Convert to pptx
pandoc deck.md -o deck.pptx

# 3. Import as native Google Slides
python3 scripts/gworkspace.py upload deck.pptx --as slide --name "Product Launch Deck" --parent FOLDER_ID
```

`scripts/gworkspace.py` sets source MIME to the pptx type and target MIME
`application/vnd.google-apps.presentation`, producing a native editable deck.

`FOLDER_ID` is optional; omit `--parent` to create in My Drive root. To target a folder,
copy the ID from its Drive URL (`.../folders/<FOLDER_ID>`), list folders with
`gog drive ls --json`, or create one with `python3 scripts/gworkspace.py mkdir "Decks"`.

### Authoring rules for pandoc slides

pandoc's pptx writer creates a new slide at each level-1 (and level-2) heading.
Structure the Markdown accordingly:

```markdown
% Product Launch Deck % Author Name % June 2026

# Section / Slide Title

Body text for the slide.

# Why Now

- Bullet one
- Bullet two
- Bullet three

# Roadmap

1. Beta in July
2. GA in September
3. Enterprise tier in Q4
```

- The `% title / % author / % date` block becomes the title slide.
- Each `#` heading becomes a new slide.
- Bulleted and numbered lists render as slide bullets.
- Keep each slide to a few bullets; pandoc does not auto-shrink overflowing text.

## Path B: md2googleslides (richer markdown decks)

Treat `md2gslides` (googleworkspace/md2googleslides) as an **optional** path only when
it is already installed and authorized. It supports a richer slide-specific Markdown
dialect (speaker notes, columns, image placement, background images), but it maintains
its own OAuth in `~/.md2googleslides` and can prompt interactively on first use.

```bash
md2gslides deck.md --title "Talk Title"
```

If `md2gslides` is absent or unauthenticated, fall back to Path A. Do not start an OAuth
flow without the user's involvement.

## Path C: raw Slides API (precise layout)

For precise control — specific layouts, text boxes at exact positions, shapes, charts,
themes — use the Slides API `presentations.batchUpdate`. Mint a token with
`python3 scripts/gworkspace.py token` and POST to
`https://slides.googleapis.com/v1/presentations/PID:batchUpdate`.

Note: the Slides API requires the Slides scope. The local `gog` token did not include it
in testing; you may need a token from a credential set authorized for
`https://www.googleapis.com/auth/presentations`. Verify scope before relying on this
path.

## When to use browser automation

- Visual QA of the rendered deck.
- Applying a theme or template that is awkward via API.
- Final manual polish.

Not the primary creation backend.

## Verification

```bash
python3 scripts/gworkspace.py meta PRESENTATION_ID      # native Slides MIME?
gog slides export --help >/dev/null 2>&1 || { echo "gog slides export unavailable"; exit 1; }
gog slides export PRESENTATION_ID --format pptx --output /tmp/deck-check.pptx --json --no-input
python3 - <<'PY'
import zipfile, re
with zipfile.ZipFile('/tmp/deck-check.pptx') as z:
    for n in sorted(z.namelist()):
        if n.startswith('ppt/slides/slide') and n.endswith('.xml'):
            t = re.findall(r'<a:t>(.*?)</a:t>', z.read(n).decode('utf-8','replace'))
            if t: print(n, t)
PY
```

Confirm:

- `mimeType` is `application/vnd.google-apps.presentation`.
- Slide count matches the number of headings.
- Titles and bullets appear in the exported slide XML.
- For visual fidelity, open the `webViewLink` in the browser.

## Safety and Approval

- "Create a deck" is approval to create the new Drive file.
- Ask before sharing, deleting, moving, or overwriting an existing presentation.
- Prefer creating a new deck over overwriting a human-edited one.

## Common Pitfalls

1. **Expecting gog to author slide content.** Local `gog slides` only does
   create/info/copy/export — no content or layout editing. Authoring goes through
   conversion or the Slides API.

2. **Missing Slides scope.** The local gog OAuth token lacked the Slides scope in
   testing. Raw Slides API calls need a token authorized for
   `https://www.googleapis.com/auth/presentations`. Drive import of a pptx does not need
   the Slides scope, only Drive — another reason the pptx import path is the default.

3. **Uploading pptx without conversion.** `gog drive upload deck.pptx` keeps it a
   PowerPoint file. Use target MIME `application/vnd.google-apps.presentation` to get
   native Slides.

4. **Overstuffed slides.** pandoc does not auto-fit text. Keep slides short; split dense
   content across multiple headings.

5. **Wrong heading levels.** If everything lands on one slide, your Markdown probably
   lacks `#` per-slide headings. Each slide needs its own top-level heading.

6. **Credential lookup fails (`missing_credentials`).** If the helper can't find gog's
   token, pass credentials explicitly: export gog's refresh token to a 0600 file with
   `gog auth tokens export <account> --output /tmp/tok.json --force`, then run the
   helper with
   `--refresh-token-file /tmp/tok.json --client-secret-file <gog credentials.json>`. The
   helper refuses explicit credential files that are group/world-readable or not owned
   by you — `chmod 600` them.

7. **Sharing is irreversible.**
   `scripts/gworkspace.py share PRESENTATION_ID --email ... --role ...` grants real
   Drive access. Confirm recipient and role with the user before using it.

## One-Shot Recipe

```bash
cat > /tmp/deck.md <<'MD'
% Demo Deck
% Your Name
% Today

# Overview

- Point one
- Point two

# Next Steps

1. Do the thing
2. Ship it
MD
pandoc /tmp/deck.md -o /tmp/deck.pptx
python3 scripts/gworkspace.py upload /tmp/deck.pptx --as slide --name "Demo Deck"
```

Return the resulting `webViewLink` to the user.
