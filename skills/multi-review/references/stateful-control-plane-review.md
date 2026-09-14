# Stateful Control-Plane Review Recipe

Use this reference for event journals, workflow engines, schedulers, durable job
systems, governance control planes, and other stateful systems whose tests may pass
while transactional guarantees remain weak.

## Review sequence

1. **Map specification claims to executable enforcement.** Build a checklist for
   append-only integrity, migrations, idempotency, provenance, acceptance,
   leases/fencing/retries/cancellation, projections/replay, budgets, communications,
   monitoring, and disaster recovery. Cite the governing requirement and implementation
   line for every conclusion.
2. **Run the repository's documented suite independently.** Preserve the exact command,
   exit code, test count, duration, and output. Passing tests are evidence about covered
   cases, not proof of the stated guarantees.
3. **Re-inventory immediately before testing and reporting.** Files may appear or change
   during a long review. Include newly discovered implementation/tests, rerun the
   complete suite, and avoid reporting an obsolete test count.
4. **Probe adversarial transaction boundaries in isolated temporary state.** Never
   mutate canonical/project state. Favor small deterministic probes over speculative
   findings.
5. **Separate mechanism from enforcement.** A stored fencing token is not fencing unless
   every side effect validates it; an evidence ID field is not evidence verification; a
   budget ledger is not an enforced envelope; a receipt row is not immutable merely
   because the event journal is.
6. **Verify review non-interference.** Prefer VCS status/diff. If the target is not a
   repository, hash in-scope source files before and after and state that hash
   comparison was the fallback. Exclude the requested report artifact from the
   no-modification comparison.

## High-value adversarial probes

### Acceptance and authority

- Attempt to set lifecycle status directly through an ordinary event payload instead of
  the governed acceptance transition.
- Use nonexistent evidence IDs.
- Set verifier identity equal to worker/actor identity.
- Check that evidence and verification records exist, satisfy the criterion, and are
  independent or deterministic.

### Idempotency

- Replay the identical request with the same key: it should return one commitment.
- Reuse the key with a different event type, aggregate, or payload: it should fail as a
  conflict, not silently return the old result.
- Check the request fingerprint excludes generated IDs/timestamps while covering all
  semantic command fields.

### Event append and projection

- Feed schema-invalid or projector-breaking payloads.
- Determine whether append and projection/checkpoint are atomic. If asynchronous by
  design, verify a durable retry/dead-letter path and that an idempotent retry advances
  the projection.
- Rebuild from the journal and compare normalized state, not just row counts.

### Migration safety

- In a temporary database, run a migration containing valid DDL followed by invalid SQL.
- Verify there is no unledgered partial schema after failure, or that a deliberately
  recorded failure state makes recovery deterministic.
- Verify applied-checksum drift rejection separately; it does not prove failure
  atomicity.

### Leases, retries, cancellation, and fencing

- Request an actively held item as a second holder; require explicit
  conflict/no-acquisition rather than returning another holder's credentials.
- Expire and reacquire; token must increase monotonically.
- Attempt a stale worker commit with the old token; every side-effect boundary must
  reject it.
- Try raising the retry cap on a later acquisition; policy should be immutable or
  separately governed.
- Verify heartbeat/renewal, release, timeout, cancellation propagation, and denial of
  post-cancellation commits.

### Receipts and budgets

- Attempt update/delete of immutable receipt/ledger records.
- Check communication expiry, authority grant, exact content hash, recipient/channel
  scope, transmission time, and provider receipt are fail-closed and appropriately
  non-null.
- Attempt overspend, reserve races, negative/NaN amounts, wrong units, and
  reset-boundary errors. A table with enum columns is not budget enforcement.

### Disaster recovery and monitoring

- Restore a backup to an isolated path, run integrity checks, compare event counts,
  replay projections there, and compare normalized state with the source snapshot.
- Check that monitoring covers the full governance contract, including retry exhaustion,
  backup age/restore proof, checkpoint age, delivery failure, budget boundaries, and
  duplicate/noise rates—not only database quick-check.

## Confirmed-defect probe recipes

These four were reproduced against a control plane whose suite was fully green and had
already been signed off. Run them by default; each targets a case an implementer's own
test characteristically misses. See `verifying-a-verdict.md` for why.

### Secrets in embedded/encoded content

A restricted-field validator that inspects **metadata keys** will not see a secret
inside **embedded source bytes**. Write a source file containing
`{"apiKey": "sk-live-..."}`, compile it into the packet/artifact, then base64-decode
every manifest entry and grep the decoded bytes. Assert on decoded content, never on the
wrapper.

### Wrong-typed vs missing governed fields

Validators commonly check `required - set(payload)` and stop. Send every required field
**present but nonsensically typed**: `current_question=12345`, `owner={'x':1}`,
`next_action=True`, `cost_to_next_evidence='banana'`. If it stores, schema validation is
structural only, not semantic.

### Concurrent identical idempotent retries

Sequential retry tests pass while concurrent ones fail. Fire two threads with an
identical idempotency key at the same receipt/commit path. Correct behavior is both
returning the same immutable record. A uniqueness error on one thread ("provider receipt
already recorded") means the pre-check and the insert are not serialized against a
concurrent twin.

### Lease validation outside the write transaction

Grep for where `validate_lease` is called relative to `BEGIN IMMEDIATE`. If validation
runs before the transaction opens, there is a real read/decide/write race even when the
naive expire-then-reacquire probe is correctly blocked. Report this as **unproven, not
disproven** — a structural weakness whose specific exploit you could not trigger. The
honest fix is making validation and commit share one transaction, which closes it
structurally instead of arguing about a race that is hard to schedule deterministically.

## Evidence and severity

- **Critical:** a declared safety boundary is bypassable, durable state can become
  unrecoverable/ambiguous, stale work can commit, or authority/acceptance can be forged.
- **Important:** a required contract is materially incomplete, unsafe under
  failure/concurrency, or only documented rather than enforced.
- **Minor:** metadata mismatch, portability issue, narrow test coverage, or overstated
  documentation without immediate boundary bypass.

Report both what passes and what fails. For reproduced defects, state the temporary
probe and observed result; do not present hypothetical concerns as confirmed behavior.
