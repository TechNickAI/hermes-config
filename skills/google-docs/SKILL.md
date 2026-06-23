---
name: google-docs
description:
  "Use when creating, importing, formatting, editing, exporting, or quality-checking
  Google Docs from agent-generated markdown or local files."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [google, docs, drive, markdown, formatting, productivity]
    related_skills: [google-sheets, google-slides]
---

# Google Docs

## Overview

Use this skill when the requested artifact is a **Google Doc**, especially when the
source material is agent-generated Markdown and the user cares about formatting.

The tested best default is **Drive import of Markdown as a native Google Doc**:

1. Write clean Markdown locally.
2. Upload it to Drive with source MIME `text/markdown` and target MIME
   `application/vnd.google-apps.document`.
3. Verify by exporting the created Doc back to `text/markdown` or `text/plain`.

This outperformed the older `pandoc -> docx -> Google Doc` path in testing: direct
Markdown import preserved headings, bold, italic, links, native tables, inline code, and
fenced code blocks more faithfully.

## Prerequisites

- **`gog` CLI** (steipete/gogcli) installed and authorized: run `gog auth login` once in
  the operating-system user's real `$HOME`. The bundled `scripts/gworkspace.py` reuses
  gog's stored OAuth refresh token, so no separate Google Cloud setup is needed.
- **`python3`** on `PATH` (the helper is stdlib-only — no `pip install` required).
- **`pandoc`** only if you use the docx path below (`brew install pandoc` on macOS).
- The helper resolves credentials in this order: explicit `--refresh-token-file` /
  `--client-secret-file`, then `GOOGLE_REFRESH_TOKEN` / `GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET` env vars, then gog's stored token. If lookup fails it exits
  with `{"error": "missing_credentials"}` — see Common Pitfalls for the manual escape
  hatch.

> **Run commands from the skill directory** so `scripts/gworkspace.py` resolves relative
> to `$PWD`, or use the full path `skills/google-docs/scripts/gworkspace.py` from the
> repo root.

> **$HOME shadowing:** some agent runtimes rewrite `$HOME` to a sandbox path where gog's
> auth doesn't exist. If credential lookup fails in that environment, point `HOME` at
> the real OS user home before invoking the helper.

## When to Use

- User asks for a Google Doc, report, memo, proposal, brief, agenda, or notes document.
- User gives Markdown and wants it turned into a formatted Google Doc.
- User asks whether to use `gog`, raw API, or browser for Docs.
- User asks to export, inspect, copy, or verify a Google Doc.

Do **not** use this for Sheets or Slides; load `google-sheets` or `google-slides`
instead.

## Decision Tree

### Best default for new docs from Markdown

Use **Drive convert-on-upload** with Markdown:

```bash
python3 scripts/gworkspace.py upload report.md \
  --as doc \
  --name "Quarterly Report" \
  --parent FOLDER_ID
```

`FOLDER_ID` is optional; omit `--parent` to create in My Drive root. To target a
specific folder, copy the ID from its Drive URL
(`https://drive.google.com/drive/folders/<FOLDER_ID>`), list folders with
`gog drive ls --json`, or create one with
`python3 scripts/gworkspace.py mkdir "Reports"`.

Why this is the default:

- No extra Python packages.
- Reuses existing `gog` OAuth refresh token.
- Produces a native Google Doc, not an uploaded `.md` or `.docx` file.
- Preserves Markdown formatting well, including code fences and tables.

### When to use newer gog directly

If `gog docs write --help` exists on the machine, prefer newer gog for **editing an
existing Doc**:

```bash
# This path requires a newer gog with docs editing commands.
gog docs write --help >/dev/null 2>&1 || { echo "gog docs write not available; use Drive import instead"; exit 1; }
gog docs create "Draft Title" --parent FOLDER_ID --json
gog docs write DOC_ID --markdown --text "$(cat report.md)" --append --json
```

Newer upstream `gog` versions include Docs editing primitives such as
`docs write --markdown`, `docs format`, `docs insert-table`, and raw Docs inspection.
Local v0.9.0 did **not** expose these commands during the 2026-06-23 test, so do not
assume they exist. Check help first.

### When to use pandoc/docx

Use `pandoc -> docx -> Google Doc` only when the source document needs Word-specific
features or the Markdown import path fails:

```bash
pandoc report.md -o report.docx
python3 scripts/gworkspace.py upload report.docx --as doc --name "Quarterly Report"
```

This path preserved headings, lists, tables, links, and basic text styling in testing,
but degraded fenced code blocks compared with direct Markdown import.

### When to use browser automation

Use the browser only for:

- visual QA after API creation,
- final manual polish that APIs cannot express quickly,
- one-off "File -> Save as Google Docs" conversion if API conversion is unavailable.

Do not use the browser as the primary document creation backend. It is slower, less
repeatable, and more fragile than Drive import or Docs API calls.

## Markdown Authoring Rules

Write the source Markdown like a real source document, not like chat output.

- One H1 title at the top.
- Use H2/H3 for structure.
- Use normal Markdown tables for tabular content.
- Use fenced code blocks with language tags.
- Use Markdown links, not bare URLs when the link text matters.
- Avoid clever HTML unless you have verified Google import preserves it.
- Keep long prose paragraphs readable; Google Docs import handles normal paragraphs
  well.

Good source (outer fence shown with `~~~` so the inner ` ``` ` code block renders
correctly):

````markdown
# Quarterly Business Review

## Executive Summary

Revenue increased **18%** quarter-over-quarter.

## Metrics

| Metric |    Q1 |    Q2 |
| ------ | ----: | ----: |
| MRR    | $420k | $496k |

```python
def churn_rate(lost, start):
    return round(lost / start * 100, 2)
```
````

## Verification

After creation, verify the Doc is native and formatting survived:

```bash
python3 scripts/gworkspace.py meta DOC_ID
python3 scripts/gworkspace.py export DOC_ID --mime text/markdown --out /tmp/doc-roundtrip.md
```

Check that:

- `mimeType` is `application/vnd.google-apps.document`.
- Round-trip Markdown still contains headings, tables, links, inline code, and fenced
  code blocks.
- The returned `webViewLink` opens as a Google Docs URL.

If exact visual polish matters, open the link in the browser and inspect the first page,
heading hierarchy, tables, and code blocks.

## Editing Existing Docs

Prefer a two-step pattern:

1. Export or inspect the existing doc.
2. Apply a targeted edit with the highest-level tool available.

Options in order:

1. Newer `gog docs write/format/find-replace` if present.
2. Raw Docs API `documents.batchUpdate` for precise paragraph/text/table/image
   operations.
3. Browser for visual/manual edits only when the API path is too expensive for the task.

Do not rewrite a human-edited Doc wholesale unless the user explicitly asks. Export
first, preserve structure, then apply a minimal diff.

## Sharing and Drive Helpers

The bundled helper also exposes Drive operations the skills reference:

```bash
python3 scripts/gworkspace.py mkdir "Reports"                 # create a folder, returns its ID
python3 scripts/gworkspace.py meta DOC_ID                     # metadata + webViewLink
python3 scripts/gworkspace.py token                           # mint an access token for raw Docs API calls
python3 scripts/gworkspace.py share DOC_ID --email a@b.com --role reader   # share — IRREVERSIBLE
```

**`share` sends a real, externally-visible grant.** Treat it like sending an email:
confirm the recipient and role with the user first, and never share without explicit
approval. `--notify` additionally emails the recipient.

For raw Docs API work (`documents.batchUpdate`), mint a token with `gworkspace.py token`
and call the REST endpoint directly.

## Safety and Approval

- If the user explicitly asks "create a Google Doc", that is approval to create the new
  Drive file.
- Ask before sharing, deleting, moving, or overwriting existing Docs. `share` is
  irreversible — confirm recipient + role first.
- Ask before making broad edits to an existing human-owned Doc unless the requested edit
  is exact and scoped.
- Prefer reversible actions: create/copy/export before destructive edits.

## Common Pitfalls

1. **Using old local gog Docs commands for formatting.** Local `gog v0.9.0` created
   title-only Docs and did not support `docs write --markdown` during testing. Check
   `gog docs --help` before relying on editing commands.

2. **Uploading Markdown without conversion.** `gog drive upload report.md` creates a
   Drive file containing Markdown, not a native Google Doc. Use Drive API target MIME
   `application/vnd.google-apps.document`, or a newer gog conversion flag if available.

3. **Wrong source MIME.** Markdown import needs source MIME `text/markdown`; guessing as
   plain text can fail or degrade formatting.

4. **Assuming docx is better.** In testing, direct Markdown import preserved code fences
   and inline code better than `pandoc -> docx -> Google Doc`.

5. **Skipping verification.** A successful upload is not enough. Confirm native Google
   Doc MIME type and round-trip export.

6. **Credential lookup fails (`missing_credentials`).** If the helper can't find gog's
   token, pass credentials explicitly: export gog's refresh token to a 0600 file with
   `gog auth tokens export <account> --output /tmp/tok.json --force`, then run the
   helper with
   `--refresh-token-file /tmp/tok.json --client-secret-file <gog credentials.json>`. The
   helper refuses credential files that are group/world-readable or not owned by you —
   `chmod 600` them.

7. **`export` returns 403.** Drive's export endpoint only works on Google-native files.
   If you accidentally uploaded a raw file (used `--as raw`), `meta` will show a
   non-Google MIME type; re-upload with the correct `--as` target.

## One-Shot Recipe

```bash
mkdir -p /tmp/gdoc-build
cat > /tmp/gdoc-build/report.md <<'MD'
# Report Title

## Summary

Write the report here.
MD
python3 scripts/gworkspace.py upload /tmp/gdoc-build/report.md --as doc --name "Report Title"
```

Return the resulting `webViewLink` to the user.
