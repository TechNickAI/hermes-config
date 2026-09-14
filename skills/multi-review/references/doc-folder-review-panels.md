# Reviewing a documentation/spec FOLDER with a multi-model panel

Proven one occasion on Fiddler's knowledge/ folder (~40 files, ~9,000 lines, 5 reviewers
× 5 model families). The SKILL.md covers single artifacts; a folder needs brief
construction. This is the recipe.

## The problem

A whole spec folder cannot go to every reviewer: `hermes -z "$PROMPT"` puts the prompt
on argv, and Linux MAX_ARG_STRLEN is **128KB per single argument** (`getconf ARG_MAX`
shows 2MB total, but the per-arg cap is what bites). `$(cat file)` does NOT dodge it. A
9k-line folder is ~300KB+ — over the cap.

## The recipe: spine + lens-specific files

1. **Define a SPINE** — the 3–8 docs every reviewer needs (README, vision, system
   overview; optionally the full philosophy set). Two sizes help: `spine` (lean, ~20KB)
   and `full_spine` for the coverage lens.
2. **Per lens, attach only the files that lens judges**:
   - quant/correctness → the math research + the algorithm component specs
   - safety red-team → invariants doc + safety/order components + incident ops
   - buildability ("you are the builder LLM — can you build without guessing?") →
     api/data/infra specs + CI/CD + coding standards
   - coverage/consistency (give to the long-context family) → full spine + **first 40–60
     lines (TL;DR+) of EVERY doc** instead of full texts — contradictions and drift live
     in the TL;DRs and headers
   - product/strategy → roadmap + decisions + the thesis research
3. **Build briefs with a Python script**, not shell: concat
   `===== FILE: knowledge/<path> =====` separators, `assert len(prompt) < 125000` per
   brief, write to `/tmp/<review>/p_<lens>.txt`. When an assert trips, trim that lens's
   file list or switch full files → head(40).
4. **Include the folder tree** (`find. -name '*.md' | sort`) in the shared header so
   reviewers can flag references to nonexistent files.
5. **Ask each reviewer for**: severity/confidence/file-location/why/smallest fix, plus a
   final verdict `ready / needs-edits / not-ready to hand to builder LLMs`.

## Launch (per SKILL.md rules, restated for this shape)

Write one `run_panel.sh` via write*file (never heredoc — the `&` guard inspects raw
command strings): `export HOME=/home/ubuntu`, a `run() { hermes -z "$(cat p*$1.txt)"
--provider "$2" -m "$3" --ignore-rules -t '' > out*$1.txt 2> err*$1.txt;
}`helper, five`run... &`lines capturing PIDs,`wait`, then `wc -c
out\_\*.txt`. Launch with `terminal(background=true, notify_on_complete=true)`. `bash
-n` the script first.

Panel that ran clean on this profile: quant→claude-opus (custom:openrouter),
safety→grok-4.3 (custom:grok), build→gpt-5.2 (custom:openrouter), coverage→gemini-pro
(custom:gemini, the long-context lens), product→think (custom:omniroute).

## Why lens-specific briefs beat one shared brief here

Different doc subsets ARE the lens: the safety reviewer judging the quoting math is
wasted tokens and anchor risk; the coverage reviewer seeing full texts of 40 files won't
fit. Matching files-to-lens is what makes a folder review both possible under ARG_MAX
and sharper than a single mega-brief.
