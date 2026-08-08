---
name: google-sheets
description:
  "Use when creating, populating, formatting, importing, exporting, or quality-checking
  Google Sheets from CSV, JSON arrays, or computed tabular data."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    requires:
      - gog CLI, authorized for Google Sheets and Drive
      - python3
    tags: [google, sheets, drive, csv, formatting, productivity]
    related_skills: [google-docs, google-slides]
---

# Google Sheets

## Overview

Use this skill when the artifact is a **Google Sheet**. Sheets is the one Google type
where the local `gog` CLI is genuinely strong: it supports values read/write, append,
clear, cell formatting, and metadata.

Two tested-good paths:

1. **`gog sheets`** for create + values + formatting. Best when you need cell
   formatting, formulas, or incremental edits.
2. **Drive convert-on-upload of CSV/TSV/XLSX** for bulk data load. Best for dumping a
   large dataset into a fresh native Sheet quickly.

## Prerequisites

- **`gog` CLI** (steipete/gogcli) installed and authorized in the operating-system
  user's real `$HOME`. Authentication commands have changed across gog releases: inspect
  `gog auth --help`, then configure credentials and add the account using the commands
  supported by the installed version. The bundled `scripts/gworkspace.py` reuses gog's
  stored OAuth refresh token for Drive import/export helpers.
- **`python3`** on `PATH` (the helper is stdlib-only — no `pip install` required).
- For `gog sheets` commands, run `gog sheets --help` once if the local gog version is
  unknown; the verified flag for 2D matrices is `--values-json`.
- The helper resolves credentials in this order: explicit `--refresh-token-file` /
  `--client-secret-file`, environment variables, then gog's stored token. If lookup
  fails it exits with `{"error": "missing_credentials"}` — see Common Pitfalls.

> **Run helper commands from the skill directory** so `scripts/gworkspace.py` resolves
> relative to `$PWD`, or use `skills/google-sheets/scripts/gworkspace.py` from the repo
> root.

> **$HOME shadowing:** some agent runtimes rewrite `$HOME` to a sandbox path where gog's
> auth doesn't exist. If credential lookup fails in that environment, point `HOME` at
> the real OS user home before invoking gog or the helper.

## When to Use

- User asks for a Google Sheet, spreadsheet, tracker, budget, dataset, or table with
  formulas.
- User has CSV/TSV/XLSX data to turn into a native Google Sheet.
- User wants header styling, conditional formatting, frozen rows, or formulas.
- User asks whether to use `gog`, raw API, or browser for Sheets.

Do **not** use this for Docs or Slides; load `google-docs` or `google-slides`.

## Path A: gog sheets (formatting + formulas)

Run with `gog` auth available.

```bash
# Create
SID=$(gog sheets create "Q2 Financials" --json --no-input 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['spreadsheetId'])")

# Write a 2D matrix — USE --values-json, not positional values
gog sheets update "$SID" "Sheet1!A1:E6" --values-json \
  '[["Region","Product","Units","Revenue","Margin"],
    ["North","Widget A",1200,48000,0.42],
    ["North","Widget B",800,32000,0.38],
    ["South","Widget A",1500,60000,0.44],
    ["South","Widget B",600,24000,0.36],
    ["West","Widget A",900,36000,0.41]]' \
  --input USER_ENTERED --json --no-input --force

# Format the header row
gog sheets format "$SID" "Sheet1!A1:E1" \
  --format-json '{"textFormat":{"bold":true},"backgroundColor":{"red":0.2,"green":0.3,"blue":0.5},"horizontalAlignment":"CENTER"}' \
  --format-fields 'userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment' \
  --json --no-input --force

# Append rows later
gog sheets append "$SID" "Sheet1!A:E" --values-json '[["East","Widget C",400,16000,0.30]]' --input USER_ENTERED --json --no-input --force

# Read back to verify
gog sheets get "$SID" "Sheet1!A1:E7" --json --no-input
```

### Formulas

With `--input USER_ENTERED`, formula strings are evaluated:

```bash
gog sheets update "$SID" "Sheet1!F1:F6" --values-json '[["Total"],["=C2*D2"],["=C3*D3"],["=C4*D4"],["=C5*D5"],["=C6*D6"]]' --input USER_ENTERED --json --no-input --force
```

Use `--input RAW` only when you want literal text, not evaluated formulas.

## Path B: Drive convert-on-upload (bulk data)

Best for loading an existing CSV/TSV/XLSX as a fresh native Sheet:

```bash
python3 scripts/gworkspace.py upload data.csv  --as sheet --name "Imported Dataset" --parent FOLDER_ID
python3 scripts/gworkspace.py upload data.xlsx --as sheet --name "Imported Workbook"
```

`FOLDER_ID` is optional; omit `--parent` to create in My Drive root. To target a folder,
copy the ID from its Drive URL (`.../folders/<FOLDER_ID>`), list folders with
`gog drive ls --json`, or create one with
`python3 scripts/gworkspace.py mkdir "Reports"`. To return the created link, parse
`.webViewLink` from the helper's JSON output.

`scripts/gworkspace.py` sets the correct source MIME automatically (`text/csv`,
`text/tab-separated-values`, xlsx) and target MIME
`application/vnd.google-apps.spreadsheet`.

The bundled helper uses an in-memory multipart upload path, so keep it to small files
(roughly 5 MB or less). For larger workbooks, prefer a current `gog drive upload`
conversion flow when supported, or use a resumable Drive upload. After bulk load, switch
to Path A (`gog sheets format`) for header styling.

## When to use raw API or browser

- **Raw Sheets API `spreadsheets.batchUpdate`**: needed for advanced features gog may
  not expose — conditional formatting rules, frozen panes, data validation, charts,
  multiple sheets/tabs with specific styling. Mint a token with
  `python3 scripts/gworkspace.py token` and POST to
  `https://sheets.googleapis.com/v4/spreadsheets/SID:batchUpdate`.
- **Browser**: visual QA, chart fine-tuning, or one-off manual cleanup only. Not the
  primary backend.

## Verification

```bash
gog sheets get "$SID" "Sheet1!A1:E7" --json --no-input          # values correct?
python3 scripts/gworkspace.py meta "$SID"                        # native Sheet MIME?
```

For financial/legal spreadsheets, verify **labels and values together** on key rows, not
just totals in isolation. A workbook can have correct numbers but one-row-shifted labels
after template filling or Drive import. Read back representative ranges from the live
Google Sheet (for example `Income Statement!A8:F20` and the final total rows) and
confirm the label-value alignment matches the source template. See
`references/xlsx-financial-workbook-verification.md` for the full checklist and command
snippets.

For formatting, read back via the API with `includeGridData=true` and confirm
`userEnteredFormat` on the styled range. A successful `format` call returns the applied
field mask; that confirms acceptance, but a grid-data read confirms the actual stored
format.

## Safety and Approval

- "Create a sheet" is approval to create the new Drive file.
- Ask before overwriting an existing populated range, clearing data, sharing, or
  deleting.
- Prefer `append` over `update` when adding rows to a live sheet to avoid clobbering.
- `gog sheets clear` is destructive; confirm the range first.

## Multi-tab workbooks + visual QA

Do not flatten logical tabs into one sheet with banner rows. Feature-detect the installed
CLI with `gog sheets --help`:

- For a new workbook, prefer `gog sheets create TITLE --sheets "Summary,Data"` when
  supported.
- For an existing workbook, prefer `gog sheets add-tab` when supported.
- Otherwise use the Sheets API `spreadsheets.batchUpdate` with an `addSheet` request.

Write each tab with `USER_ENTERED` values. Advanced formatting can use
`spreadsheets.batchUpdate` requests such as `repeatCell`, `updateDimensionProperties`,
`addConditionalFormatRule`, `setBasicFilter`, and `updateSheetProperties`.

### Never strip formulas to "values only"

Writing `"'" + formula` to neuter a formula renders as literal `'=SUM(H3:H14)` text in
the cell. That is the single most visible way to ship a broken-looking sheet. Keep real
formulas and let `USER_ENTERED` evaluate them.

### Mandatory verification before handing over

1. **Literal-formula and error scan** — read every tab with
   `gog sheets get ... --render FORMATTED_VALUE` and assert no displayed cell starts
   with `=` or `'=`, and none unexpectedly contain `#REF!`, `#ERROR!`, `#NAME?`,
   `#DIV/0!`, `#VALUE!`, `#NUM!`, or `#N/A`.
2. **Formula liveness** — read the same range with
   `gog sheets get ... --render FORMULA` and confirm required formulas are still present.
3. **Visual QA** — export each tab to PDF and inspect **every page**. Use a multipage
   rasterizer such as `pdftoppm` rather than a first-page-only conversion:
   ```bash
   curl -sL -H "Authorization: Bearer $TOK" \
     "https://docs.google.com/spreadsheets/d/$SID/export?format=pdf&gid=$GID&portrait=false&fitw=true&gridlines=false" -o tab.pdf
   pdftoppm -png -r 150 tab.pdf tab-page
   ```
   Then inspect the PNG with a vision tool. This catches what APIs cannot: truncated
   columns, sentences split mid-thought across rows, header text that contradicts the
   content ("THE THREE BUCKETS" above four items), unreadable CODE_NAME labels. Note:
   PDF export renders `verticalAlignment: MIDDLE` as top-aligned — verify alignment via
   `includeGridData=true&fields=...effectiveFormat` instead, not the PDF.

### Layout rules

- Long prose goes in ONE cell with `wrapStrategy: WRAP` + a wide column, never split
  across consecutive rows — that reads as a formatting bug.
- Set explicit `pixelSize` row heights for wrapped narrative rows so text can breathe.
- `hideGridlines: True` on summary/report tabs; keep them on data tabs.
- Human-readable labels beat unexplained internal codes.
- For financial summaries include a **control total** proving the parts sum to the
  source total — reviewers flag its absence immediately.
- Make counts consistent everywhere; if summary and detail counts differ, state why.

### Final-artifact hygiene

Prefer updating one draft rather than creating a new file on every attempt. Track the
final spreadsheet ID and confirm sharing applies to that ID. Do not delete or trash
superseded drafts unless cleanup was explicitly authorized; if it was, verify the final
artifact still exists after cleanup.

## Common Pitfalls

0. **XLSX formula cache trap on funder/legal deliverables.** Libraries like `openpyxl`
   write formulas (`=SUM(...)`) but do not calculate and store cached results. Excel may
   recalculate on open, but Quick Look, Numbers previews, Drive conversion, and some
   Google Sheets imports may show `$0`, blank, or stale values. For external-facing
   financial workbooks where values must display correctly everywhere, prefer writing
   static computed values from Python instead of formulas. If formulas are required,
   verify after upload by reading the live Google Sheet values with `gog sheets get`.
   Also ensure labels do not start with `=` (e.g. `= SOIL NET...`) or spreadsheet tools
   will treat them as formulas.

1. **Positional values instead of `--values-json`.** In `gog v0.9.0`,
   `gog sheets update SID RANGE '[[...]]'` treats the JSON string as a single flat
   column and writes garbage like `[["Region"`. Always pass a 2D matrix via
   `--values-json`.

2. **Range smaller than data.** `update` errors with "tried writing to row N" if the A1
   range is smaller than the matrix. Size the range to match, or write to a single
   anchor cell when the API allows.

3. **Using the wrong input mode.** Keep `--input USER_ENTERED` explicit for typed values
   and live formulas; use `RAW` only for literal strings. Do not rely on a release's
   implicit default.

4. **Mixing wrapper flags with gog flags.** Some Google Workspace wrappers use
   `--values` for JSON matrices; the `gog` CLI flag is `--values-json`. Don't mix them.

5. **Uploading CSV without conversion.** `gog drive upload data.csv` keeps it a CSV
   file. Use the convert path (target MIME spreadsheet) to get a native Sheet.

6. **Assuming advanced formatting is in gog.** Conditional formatting, frozen panes,
   validation, and charts may need raw `spreadsheets.batchUpdate`. Check
   `gog sheets --help` first.

7. **Credential lookup fails (`missing_credentials`).** If the helper can't find gog's
   token, pass credentials explicitly: export gog's refresh token to a 0600 file. On
   current gog releases use
   `gog auth tokens export <account> --out /tmp/tok.json --overwrite`; inspect
   `gog auth tokens export --help` on older releases. Then run the
   helper with
   `--refresh-token-file /tmp/tok.json --client-secret-file <gog credentials.json>`. The
   helper refuses explicit credential files that are group/world-readable or not owned
   by you — `chmod 600` them. These flags work before or after the subcommand. For a gog
   named client or a non-default gog home, use `--gog-client <name>` /
   `--gog-home <dir>` (or the `GOG_CLIENT` / `GOG_HOME` env vars).

8. **Sharing is externally consequential.** Permissions can be revoked, but
   `scripts/gworkspace.py share SHEET_ID --email ... --role ...` grants real Drive
   access. Confirm recipient and role with the user before using it.

9. **Array index vs. A1 row number (off-by-one).** Returned values are a 0-indexed array
   relative to the requested range. Compute
   `sheet_row = requested_range_start_row + array_index`; `index + 1` is correct only
   when the requested range starts at row 1. Read back the changed cell and its stable
   row label before treating the update as successful.

## One-Shot Recipe

```bash
SID=$(gog sheets create "New Tracker" --json --no-input 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['spreadsheetId'])")
gog sheets update "$SID" "Sheet1!A1:C1" --values-json '[["Name","Status","Owner"]]' --input USER_ENTERED --json --no-input --force
gog sheets format "$SID" "Sheet1!A1:C1" --format-json '{"textFormat":{"bold":true}}' --format-fields 'userEnteredFormat.textFormat.bold' --json --no-input --force
echo "https://docs.google.com/spreadsheets/d/$SID/edit"
```
