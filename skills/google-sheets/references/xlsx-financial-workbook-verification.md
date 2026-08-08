# XLSX Financial Workbook Verification

Use this when creating or importing external-facing financial/legal spreadsheets,
especially IRS, grant, funder, or board workbooks.

## Why this matters

Two classes of spreadsheet failures are easy to miss:

1. **Formula cache failures.** Python libraries such as `openpyxl` can write formulas
   but do not calculate and store cached results. Excel may recalculate on open, but
   Google Drive conversion, Quick Look, Numbers previews, or other consumers can display
   `$0`, blanks, or stale cached values.
2. **Template row-shift failures.** Values can be numerically correct but attached to
   the wrong template label (for example, donations landing on `Other revenue` instead
   of `Gifts, grants & contributions`) if row indexes are off by one.

## Default build rule

For deliverables that a funder, lawyer, accountant, or grant writer will open, prefer
**static computed values** over live formulas unless the user explicitly needs formulas.

If formulas are required:

- Set workbook calc mode/full-calc flags if available.
- Open/recalculate with a real spreadsheet engine if possible.
- Upload/import only after confirming cached values survive.

## Verification checklist

After building locally:

```bash
uv run --with openpyxl python - <<'PY'
from openpyxl import load_workbook
p='PATH.xlsx'
wb_values = load_workbook(p, data_only=True)
wb_formulas = load_workbook(p, data_only=False)
for ws in wb_formulas.worksheets:
    formulas=[]
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith('='):
                formulas.append((ws.title, c.coordinate, c.value))
    print(ws.title, 'formula_count', len(formulas), formulas[:5])
PY
```

If using static values, formula count should be zero.

After Drive import to Google Sheets, verify key rows live:

```bash
gog sheets get "$SID" "Income Statement!A8:F20" --json --no-input
gog sheets get "$SID" "Income Statement!A39:F41" --json --no-input
gog sheets get "$SID" "Balance Sheet!A8:D33" --json --no-input
```

Check:

- The row labels match the values.
- Totals show non-zero expected amounts.
- No note text overwrote template rows.
- The returned link points to the verified final copy. Keep superseded drafts clearly
  named, and delete/trash them only when cleanup was explicitly authorized.

## Pitfalls

- Labels beginning with `=` become formulas. Use `SOIL NET...`, not `= SOIL NET...`.
- Merged cells in templates can swallow values if notes are inserted over total rows.
- A successful upload only proves file creation; it does not prove import fidelity.
