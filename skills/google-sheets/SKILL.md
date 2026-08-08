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
      - gog CLI, authorized via `gog auth login`
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

- **`gog` CLI** (steipete/gogcli) installed and authorized: run `gog auth login` once in
  the operating-system user's real `$HOME`. The bundled `scripts/gworkspace.py` reuses
  gog's stored OAuth refresh token for Drive import/export helpers.
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

After bulk load, switch to Path A (`gog sheets format`) for header styling.

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

## Multi-tab workbooks + visual QA (learned the hard way)

`gog v0.9.0` has **no add-sheet command**. Do NOT respond by flattening tabs into one
sheet with `----- SECTION -----` banners — that produces an unusable document. Create
all tabs in ONE `spreadsheets.create` call via the raw API:

```python
api("https://sheets.googleapis.com/v4/spreadsheets", {
  "properties": {"title": TITLE},
  "sheets": [{"properties": {"sheetId": 0, "title": "Summary",
              "gridProperties": {"rowCount": 40, "columnCount": 6,
                                 "frozenRowCount": 1}}}, ...]})
```

Then `values:batchUpdate` with `"valueInputOption": "USER_ENTERED"` and one entry per
`'Tab Name'!A1`. Formatting goes through `:batchUpdate` (`repeatCell`,
`updateDimensionProperties`, `addConditionalFormatRule`, `setBasicFilter`,
`updateSheetProperties.gridProperties.hideGridlines`).

### Never strip formulas to "values only"

Writing `"'" + formula` to neuter a formula renders as literal `'=SUM(H3:H14)` text in
the cell. That is the single most visible way to ship a broken-looking sheet. Keep real
formulas and let `USER_ENTERED` evaluate them.

### Mandatory verification before handing over

1. **Literal-formula scan** — read every tab with `valueRenderOption=FORMATTED_VALUE`
   and assert no displayed cell starts with `=` or `'=`, and none contain `#REF`,
   `#ERROR`, `#NAME`. A formula that works shows `$2,380.00`, never `=SUM(...)`.
2. **Formula liveness** — read the same range with `valueRenderOption=FORMULA` and
   confirm the underlying `=SUM(...)` is still there.
3. **Visual QA** — export each tab to PDF and actually look at it:
   ```bash
   curl -sL -H "Authorization: Bearer $TOK" \
     "https://docs.google.com/spreadsheets/d/$SID/export?format=pdf&gid=$GID&portrait=false&fitw=true&gridlines=false" -o tab.pdf
   sips -s format png --resampleWidth 1700 tab.pdf --out tab.png
   ```
   Then inspect the PNG with a vision tool. This catches what APIs cannot: truncated
   columns, sentences split mid-thought across rows, header text that contradicts the
   content ("THE THREE BUCKETS" above four items), unreadable CODE_NAME labels. Note:
   PDF export renders `verticalAlignment: MIDDLE` as top-aligned — verify alignment via
   `includeGridData=true&fields=...effectiveFormat` instead, not the PDF.

### Layout rules that survived review

- Long prose goes in ONE cell with `wrapStrategy: WRAP` + a wide column, never split
  across consecutive rows — that reads as a formatting bug.
- Set explicit `pixelSize` row heights for wrapped narrative rows so text can breathe.
- `hideGridlines: True` on summary/report tabs; keep them on data tabs.
- Human-readable labels (`Personal funds`) beat internal codes (`PERSONAL`).
- For financial summaries include a **control total** proving the parts sum to the
  source total — reviewers flag its absence immediately.
- Make counts consistent everywhere (don't say "127 transactions" up top and "79
  transactions" in the total; state what the difference is).

### Don't litter Drive

Each rebuild attempt creates a NEW spreadsheet. Track the current id, and
`gog drive delete <id> --force` every superseded draft before sharing. Verify the
survivor with `gog drive get <id>` (check `trashed`) and confirm the share landed on the
FINAL id — sharing an early draft while polishing a later one is an easy, embarrassing
miss.

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

3. **Forgetting `--input USER_ENTERED`.** Without it, numbers and formulas may be stored
   as text. Use `USER_ENTERED` for typed values and live formulas; `RAW` only for
   literal strings.

4. **Mixing wrapper flags with gog flags.** Some Google Workspace wrappers use
   `--values` for JSON matrices; the `gog` CLI flag is `--values-json`. Don't mix them.

5. **Uploading CSV without conversion.** `gog drive upload data.csv` keeps it a CSV
   file. Use the convert path (target MIME spreadsheet) to get a native Sheet.

6. **Assuming advanced formatting is in gog.** Conditional formatting, frozen panes,
   validation, and charts may need raw `spreadsheets.batchUpdate`. Check
   `gog sheets --help` first.

7. **Credential lookup fails (`missing_credentials`).** If the helper can't find gog's
   token, pass credentials explicitly: export gog's refresh token to a 0600 file with
   `gog auth tokens export <account> --output /tmp/tok.json --force`, then run the
   helper with
   `--refresh-token-file /tmp/tok.json --client-secret-file <gog credentials.json>`. The
   helper refuses explicit credential files that are group/world-readable or not owned
   by you — `chmod 600` them. These flags work before or after the subcommand. For a gog
   named client or a non-default gog home, use `--gog-client <name>` /
   `--gog-home <dir>` (or the `GOG_CLIENT` / `GOG_HOME` env vars).

8. **Sharing is irreversible.**
   `scripts/gworkspace.py share SHEET_ID --email ... --role ...` grants real Drive
   access. Confirm recipient and role with the user before using it.

9. **Array index vs. A1 row number (off-by-one).** `gog sheets get` returns values as a
   0-indexed array where index 0 is the header row. A1 notation is 1-indexed from the
   top of the sheet: the header is row 1, the first data row is row 2. So array index N
   maps to A1 row N+1. When updating a single cell by row, always compute
   `sheet_row = array_index + 1`. Failing to add 1 writes to the row above the target --
   and since `update` silently overwrites whatever is there, the only safety net is
   reading back the updated cell and checking that the task/label in the same row
   matches what you intended to update.

## One-Shot Recipe

```bash
SID=$(gog sheets create "New Tracker" --json --no-input 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['spreadsheetId'])")
gog sheets update "$SID" "Sheet1!A1:C1" --values-json '[["Name","Status","Owner"]]' --input USER_ENTERED --json --no-input --force
gog sheets format "$SID" "Sheet1!A1:C1" --format-json '{"textFormat":{"bold":true}}' --format-fields 'userEnteredFormat.textFormat.bold' --json --no-input --force
echo "https://docs.google.com/spreadsheets/d/$SID/edit"
```
