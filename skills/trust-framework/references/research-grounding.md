# Research Grounding

Every construct in the trust framework maps to an established, named precedent. This is
the "why each design choice exists" layer — useful when defending the design or
extending it.

## The mapping

| Framework construct                   | Anchored in                                                                                   | What it contributes                                   |
| ------------------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| L1 approval-required                  | Human-in-the-loop; EU AI Act "effective human oversight" for high-risk systems                | Per-action approval for the highest-stakes work       |
| L2 act-within-guardrails-report-after | Human-on-the-loop; SAE-style "conditional automation"; Delegation-of-Authority signing limits | Autonomy bounded by explicit caps, with monitoring    |
| L3 autonomous-with-periodic-review    | Human-out-of-the-loop; "trust but verify"; SRE error budgets                                  | Independent operation with sampled audit              |
| Reversibility classification          | One-way vs two-way doors + blast radius                                                       | The primary act-vs-ask axis                           |
| Promotion by track record per bucket  | Apprenticeship/competency models; probationary periods                                        | Earned, demonstrated, domain-specific autonomy        |
| Automatic demotion                    | SRE error budgets                                                                             | Trust decays the moment reliability does              |
| Confidence gate scaled to risk        | Selective prediction / "learning to defer"                                                    | Abstain-and-escalate when uncertain, scaled to stakes |
| Skill buckets with scoped caps        | Principle of least privilege; RACI/RAPID decision rights                                      | Minimum authority; agent Performs, human Decides      |
| Govern/Map/Measure/Manage loop        | NIST AI Risk Management Framework                                                             | The audit-and-improve cadence                         |
| Auditable config + ledger             | ISO/IEC 42001 (AI management system)                                                          | A documented process a human can inspect and modify   |

## The named frameworks

**NIST AI Risk Management Framework (AI RMF 1.0 + Generative AI Profile).** Four
functions — Govern, Map, Measure, Manage. The GenAI Profile explicitly names "Human-AI
Configuration" risk (over-reliance / automation bias) as a thing to manage. The periodic
self-audit is the Measure/Manage step; every bucket having an owner, a risk class, a
measured signal, and a rollback path is the Govern step.

**EU AI Act (risk tiers).** Unacceptable / high / limited / minimal risk, with high-risk
systems required to have _effective human oversight_. The framework borrows the
proportionality principle (oversight scales with consequence) and the carve-out logic
that narrow, procedural, review-assisting tasks are inherently lower-tier — good L2/L3
candidates.

**SAE levels of automation (J3016) + agent levels-of-autonomy taxonomies.** The
canonical analogy: each level corresponds to a different human attention requirement.
The key adaptation is that autonomy levels mirror the _human's role_ (approver →
observer), not the agent's raw capability. Capability is not permission.

**One-way vs two-way doors.** The reversibility heuristic: irreversible decisions get
heavyweight review; reversible ones move fast. Adopted as the primary classification
axis.

**Blast radius (SRE / security).** Scope of harm: self → single record → owned systems →
external people → public. Used as the multiplier on reversibility.

**Principle of least privilege + RACI / RAPID.** Grant minimum scopes; expand on
demonstrated need. Separate _who decides_ from _who executes_ — the agent is
Responsible/Performs while the human stays Accountable/Decides.

**Selective prediction / learning to defer.** Models should abstain and hand off when
uncertain. Production handoff thresholds scale with consequence (~0.9 for high-risk /
one-way, ~0.7 for low-risk / reversible). LLMs run overconfident, so calibrate stated
confidence against realized outcomes.

**Organizational trust models.** Probationary periods → the mandatory L1 start.
Delegation-of-Authority signing limits → per-level authority caps. Apprenticeship /
competency ladders (apprentice → journeyman → master) → domain-specific promotion.
Performance-review cadences → the periodic review. "Trust but verify" → autonomy plus
persistent sampling.

**SRE error budgets.** Exceed the failure budget and autonomy auto-freezes until
reliability recovers. Adopted directly as the automatic-demotion trigger.

## Three load-bearing principles

1. **Capability ≠ permission.** Promotion is earned per skill area and gated by
   reversibility, never granted because the agent is technically able.
2. **Oversight must stay meaningful.** Avoid rubber-stamp L1 (per-action approval at
   scale trains the human to stop reading) and L2/L3 complacency (sampled audit, surface
   uncertainty, review reasoning not just output).
3. **Trust is non-monotonic.** Error budgets and review cadences demote as readily as
   track records promote.
