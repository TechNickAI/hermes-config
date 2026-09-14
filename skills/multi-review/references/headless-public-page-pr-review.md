# Headless multi-model review for a public static page PR

Session pattern captured from a HeartCentered AI public page PR.

## When this helps

Use this when a public-facing static page or PR needs a real multi-model panel and the
artifact is pure text/HTML small enough for a one-shot review, but large enough that
reviewer prompts should be prepared carefully.

## Working recipe

1. Build a bounded review artifact first.
   - Strip unchanged boilerplate that reviewers do not need, such as analytics IIFEs.
   - Keep the actual page body, metadata, schema, links, and any areas likely to affect
     public trust.
2. Confirm model families actually route before claiming multi-model review.
   - Run tiny probes like "Reply with exactly OK" against each provider/model alias.
   - Only call it multi-model if distinct families actually responded.
3. Write prompt files programmatically, not with fragile shell heredocs, when the
   artifact contains HTML entities, ampersands, math, or long markup.
   - A terminal wrapper may mistake literal `&` in heredocs or command text for shell
     backgrounding.
   - Use Python or a file-writing tool to compose `/tmp/prompt_<lens>.txt` files, then
     call `hermes -z "$(cat /tmp/prompt_<lens>.txt)"...` only if the prompt is
     comfortably under argv limits.
4. Use one independent prompt per lens.
   - Example lenses: contrarian/license/reputation, content/math accuracy/voice,
     structure/SEO/accessibility.
   - Ask for severity, confidence, evidence/location, why it matters, smallest fix, and
     verdict.
5. Run reviewers as headless, tool-disabled one-shots.
   - Include `--ignore-rules -t ''`.
   - Redirect stdout/stderr to separate files for later synthesis.
   - For bounded long-running reviews, use background sessions with notify-on-complete,
     or wait/poll explicitly.
6. Synthesize mechanically.
   - Fix all real medium-or-higher findings before proceeding.
   - Low-risk public-page polish can usually be auto-fixed when the user asked to get
     the work into a PR.
   - Reject false positives explicitly rather than blindly adding complexity.
7. Re-run deterministic local checks after applying review feedback.
   - Parse JSON-LD, run prettier/pre-commit, scan target strings, and verify
     link/schema/a11y changes.

## Public page findings that were worth fixing

- Search/social metadata can overclaim even when the body has disclaimers. Avoid
  ambiguous SEO phrases that may surface without context.
- Attribution sections should make license boundaries explicit when citing a source
  whose docs/taxonomy use share-alike licensing.
- Repeated equations need accessible treatment too, not just the hero formula.
- Typed Schema.org objects for `isBasedOn`/`citation` are stronger than bare strings
  when attribution matters.

## Pitfalls

- Do not let rejected concept/demo pages leak into the PR. Delete or leave them
  untracked, then stage public files explicitly.
- After fuzzy patching HTML, inspect the diff for nearby accidental class changes,
  especially mobile touch targets and accessibility attributes.
- Do not stage `.hermes/` plan artifacts into public website repos unless the repo
  intentionally tracks them.
