# DelegateTask-based panel review (subagent mechanism)

This is the `delegate_task` native alternative to the `hermes -z` headless panel
documented in `<agent-c>-profile-invocation.md`. Use whichever fits the moment:

- **`hermes -z` headless calls** when you need exact model/family pinning (GPT vs Gemini
  vs Grok specifically) and are fine running them sequentially yourself.
- **`DelegateTask` batch mode** when you want automatic backgrounding and don't need to
  guarantee three specific model families (children inherit the parent model chain
  unless the profile pins `delegation.model`). Good for "read this as three different
  lenses" reviews (e.g. recipient-empathy, negotiation-positioning, voice/clarity) where
  lens diversity matters more than family diversity.

## CRITICAL gotcha: batch mode ignores top-level context

When calling `DelegateTask` with a `tasks` array, **the top-level `goal` and `context`
fields are silently ignored** — each task dict needs its own fully self-contained
`context`. Subagents have zero access to the parent conversation and cannot read
anything that only exists as conversation text (no file was written for it).

Symptom hit in production: a 3-way panel review of a WhatsApp draft was launched with
the full draft text in the top-level `context` field and only meta-instructions ("review
as David and James...") in each task's own `context`. One subagent spent its whole turn
searching the filesystem, clipboard, and session history for "the draft" and came back
empty-handed, correctly refusing to fabricate a review of text it never received. Wasted
a full round-trip.

**Fix:** inline the complete artifact text into _every_ task's own `context` field, even
when it's identical across all 3-4 tasks. Don't rely on a shared top-level field in
batch mode. If the artifact is long, it's fine to repeat it verbatim in each task —
subagents are isolated and cheap.

## Working pattern

```
tasks: [
  {goal: "Empathy review as recipient X", context: "<full artifact text>\n\nReview lens:..."},
  {goal: "Negotiation/positioning review", context: "<full artifact text>\n\nReview lens:..."},
  {goal: "Voice and clarity review", context: "<full artifact text>\n\nReview lens:..."},
]
```

Synthesize the three summaries yourself into one merged set of edits, same as the
`hermes -z` panel synthesis step.

## Scope each reviewer so it can actually finish

A background subagent that exceeds its wall-clock budget returns **nothing** — not
partial findings, not a summary. All of its work is lost. Worked case: a reviewer asked
to verify eight distinct adversarial properties _and_ run a full isolated test suite
timed out at 1200s after 49 API calls with zero usable output. A second reviewer given a
narrower brief against the same code returned a complete verdict in 568s.

- **One coherent question per reviewer.** If the goal string joins eight things with
  "and", split it across reviewers or cut the low-value items.
- **Prefer verify-this-claim over audit-everything.** "Reproduce finding C3 and report
  whether it holds" finishes. "Adversarially verify the entire control plane" does not.
- **Budget the I/O.** A reviewer that must copy a tree, build an environment, and run a
  suite before it starts reasoning has spent most of its budget on setup. Do that in the
  parent and hand the child a bounded brief plus a path.
- **Assume no partial credit.** Write the goal so a reviewer answering at 60% of budget
  is the expected case, not the lucky one.
- **Open-ended web research is the highest-risk reviewer shape.** It has no natural
  stopping point, so it burns the whole budget and returns nothing. In one batch of
  three, the two "go find historical examples" / "research what practitioners actually
  do" tasks both timed out at 1200s (37 and 33 API calls, zero output), while the
  bounded _methodology critique_ task completed with a full verdict. If you need broad
  discovery, do it in the parent with normal tools and hand the child a bounded brief.
- **When a batch times out, retry with `hermes -z` headless one-shots instead.** They
  are synchronous, return in 15-60s for a self-contained packet, and cannot silently
  lose their work. A 3-family panel on a bounded packet completed in under two minutes
  where subagents had burned 20 minutes and returned nothing usable.

## Batch mode for DESIGN, not just review

The same mechanism works for going wide on a novel design problem before building. When
two attempts have already missed and you're pattern-matching to training data, dispatch
3 designers with genuinely orthogonal lenses rather than 3 reviewers with overlapping
ones.

What made the lenses carve cleanly for a chief-of-staff system:

1. **Memory / context engineering** — the state substrate, how it evolves and compacts
2. **Orchestration / autonomy** — the loop, attention allocation, runaway detection,
   budget
3. **Human experience** — where the principal speaks, how decisions surface, anti-noise

Give each the same background but a _different question_, and state the constraints
negatively where it matters ("do not design a database", "do not propose JSON as the
primary store") — otherwise all three converge on the same familiar architecture and
you've paid for one answer three times. Synthesize to one coherent design, explicitly
not a merge; Nick's framing: _"This is not a Frankenstein, this is go wide for ideas and
then come up with an elegant system."_
