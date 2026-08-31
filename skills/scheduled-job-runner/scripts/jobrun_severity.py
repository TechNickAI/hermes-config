#!/usr/bin/env python3
"""
jobrun severity contract (v2) — classification, reconciliation, and dedup.

WHY THIS MODULE EXISTS
----------------------
v1 rendered severity from ``spec.critical``, a hand-set boolean describing the
JOB. Every failure of a flagged job therefore rendered "🛑 CRITICAL — this job
moves real money" at equal volume, whether the cause was a 120s network timeout
or a guard that had genuinely stopped guarding.

Two consequences seen in practice:

  * A watchdog on a short schedule produced dozens of timeout failures in a
    single day, each rendering its own red card. The job itself was healthy —
    its typical runtime was a small fraction of its ceiling — and the timeouts
    came from an upstream outage. Every duration landed on the ceiling exactly,
    which is the signature of a blocked call killed by the harness rather than
    organic slowness. Many identical red cards, one condition, no defect.
  * A guard job carried ``critical = true`` and the line "This job moves real
    money" while its wrapper set the broker's paper-trading flag. The alarm was
    factually false.

Severity is a property of the RUN, not of the script. This module makes that
structural rather than advisory.

TWO EXIT CODE NAMESPACES — DO NOT COLLAPSE THEM
------------------------------------------------
``jobrun.py`` uses 0/2/3/4/5/6 for its OWN outward exit status to the scheduler.
This module classifies the CHILD process's exit code. They are different
namespaces on different process boundaries. The child band (10/20/30) was chosen
to be clear of every reserved range in BOTH directions:

    1        the fleet's existing hard-failure convention. NEVER redefine as
             WARNING: it would silently downgrade every legacy failure.
    2        shell builtin misuse; Python argparse; LSB "invalid argument"
    3-9      RESERVED, FORBIDDEN for domain meaning. See below.
    64-78    BSD sysexits.h (deprecated and nonportable, but still emitted)
    126,127  not executable / not found
    128+N    signal encoding
    200+     systemd launch failures (EXIT_CHDIR, EXIT_EXEC)

WHY 3-9 ARE FORBIDDEN FOR DOMAIN MEANING
-----------------------------------------
A job once encoded the research state "REINSTATE: positive-carry thesis holds"
as exit code 3. It "failed" 12 times in the retention window while working
perfectly. A script that invents private semantics inside the band the runner
reads as failure will be misclassified forever, and the author's own notes said
never to do this. Domain state goes in the sentinel's ``reason_code``, never in a
low exit code. This module raises on 3-9 so the mistake is loud.

REJECTED: the Nagios 0/1/2/3 ABI. 2 collides with shell-usage semantics, and
Nagios's 3=UNKNOWN is not monotonically more severe than 2=CRITICAL.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Severity ladder
# --------------------------------------------------------------------------
# Ordered. Comparison is by index, so "raise but never lower" is expressible.
HEALTHY = "healthy"
NOTEWORTHY = "noteworthy"
DEGRADED = "degraded"
CRITICAL = "critical"

LADDER = [HEALTHY, NOTEWORTHY, DEGRADED, CRITICAL]
_RANK = {name: i for i, name in enumerate(LADDER)}

# Child exit code -> severity. Deliberately sparse.
CHILD_EXIT_SEVERITY = {
    0: HEALTHY,
    10: NOTEWORTHY,
    20: DEGRADED,
    30: CRITICAL,
}

# Codes a script must not use to mean anything domain-specific.
FORBIDDEN_DOMAIN_CODES = range(3, 10)

SENTINEL_PREFIX = "@@JOBRUN_RESULT@@"
SENTINEL_SCHEMA = "jobrun.result/v1"


def rank(severity: str) -> int:
    """Position on the ladder. Unknown severities sort as DEGRADED."""
    return _RANK.get(severity, _RANK[DEGRADED])


def at_least(a: str, b: str) -> str:
    """The more severe of two severities."""
    return a if rank(a) >= rank(b) else b


# --------------------------------------------------------------------------
# Money classification — declared AND detected, never one alone
# --------------------------------------------------------------------------
# A paper-trading job got a real-money alarm because ONE hand-set boolean
# decided it. The reverse error (a live job labelled paper) is far worse. So the
# spec must DECLARE, the runner independently DETECTS from the script, and a
# disagreement is a hard spec error rather than a silent pick-a-winner.

MONEY_LIVE = "live"
MONEY_PAPER = "paper"
MONEY_NONE = "none"
MONEY_VALUES = (MONEY_LIVE, MONEY_PAPER, MONEY_NONE)

# Ordered most-specific-first. A paper marker anywhere is strong evidence, and
# is checked BEFORE the live endpoints, because a paper script frequently also
# mentions the live host in a comment or a fallback branch.
_PAPER_MARKERS = (
    # Matches both shell (``export X=true``) and Python
    # (``os.environ['X'] = 'true'``) forms. Some paper-trading jobs use the
    # Python form, so a shell-only pattern silently misses real paper markers.
    re.compile(r"ALPACA_PAPER_TRADE[\"'\]\s]*=\s*[\"']?true", re.I),
    re.compile(r"paper-api\.alpaca\.markets", re.I),
    re.compile(r"\bPAPER_TRADING[\"'\]\s]*=\s*[\"']?true", re.I),
    re.compile(r"\bsandbox\.", re.I),
)
_LIVE_MARKERS = (
    re.compile(r"ALPACA_PAPER_TRADE[\"'\]\s]*=\s*[\"']?false", re.I),
    re.compile(r"(?<!paper-)\bapi\.alpaca\.markets", re.I),
    re.compile(r"\bLIVE_TRADING[\"'\]\s]*=\s*[\"']?true", re.I),
)


# Matches an invocation of another script, so detection can follow one level past
# a shell wrapper.
_INVOKE = re.compile(
    r"""(?:python3?|uv\s+run(?:\s+--\S+)*|bash|sh)\s+
        (?:-\S+\s+)*
        ([A-Za-z0-9_./-]+\.(?:py|sh))""",
    re.X,
)


def _scan(text: str) -> str | None:
    for pat in _PAPER_MARKERS:
        if pat.search(text):
            return MONEY_PAPER
    for pat in _LIVE_MARKERS:
        if pat.search(text):
            return MONEY_LIVE
    return None


def detect_money(script_text: str, base_dir=None, _depth: int = 0) -> str:
    """
    Infer whether a script touches live money, from its own source.

    Returns MONEY_LIVE, MONEY_PAPER, or MONEY_NONE. Detection is EVIDENCE, not
    a verdict: reconcile_money() decides. Paper markers win over live markers
    because paper scripts routinely reference the live host in comments and
    fallback branches, while the converse is rare.

    FOLLOWS ONE LEVEL OF INVOCATION. Found the hard way in a recent incident: a
    set of guard-job specs pointed at a shell wrapper, whose only relevant
    content invoked a Python script. The live markers (``api.alpaca.markets``,
    "missing Alpaca live credentials") lived in the .py. Scanning only the named
    script returned MONEY_NONE for three jobs that genuinely touch a live
    account — a false negative in the DANGEROUS direction, which is precisely
    the error this whole mechanism exists to prevent.

    ``base_dir`` is the profile root used to resolve a relative invocation.
    Depth is capped at 2 files: deeper chains are rare, and an unbounded walk
    would follow arbitrary user scripts.
    """
    if not script_text:
        return MONEY_NONE
    hit = _scan(script_text)
    if hit:
        return hit

    if base_dir is None or _depth >= 1:
        return MONEY_NONE

    from pathlib import Path as _P

    base = _P(base_dir)
    for m in _INVOKE.finditer(script_text):
        rel = m.group(1)
        for cand in (base / rel, base / "scripts" / _P(rel).name):
            try:
                if cand.is_file():
                    sub = detect_money(
                        cand.read_text(errors="replace"), base_dir, _depth + 1
                    )
                    if sub != MONEY_NONE:
                        return sub
            except OSError:
                continue
    return MONEY_NONE


class MoneyMismatch(Exception):
    """Declared money class contradicts what the script actually does."""


def reconcile_money(declared: str | None, detected: str) -> str:
    """
    Reconcile the spec's declaration against the runner's detection.

    Rules, in order:
      * Nothing declared -> trust detection. Migration path for legacy specs.
      * Agreement -> that value.
      * Declared live, detected paper/none -> ALLOWED, with the declaration
        winning. Over-declaring is the safe direction: it can only make an
        alert louder than warranted, never quieter.
      * Declared paper/none, detected live -> HARD ERROR. This is the direction
        that silences a real-money alarm, and it is exactly the failure this
        function exists to prevent.
    """
    if declared is None:
        return detected
    if declared not in MONEY_VALUES:
        raise MoneyMismatch(
            f"money must be one of {MONEY_VALUES}, got {declared!r}"
        )
    if declared == detected:
        return declared
    if declared == MONEY_LIVE:
        return MONEY_LIVE
    if detected == MONEY_LIVE:
        raise MoneyMismatch(
            f"spec declares money={declared!r} but the script contains live "
            f"trading markers (detected={detected!r}). Refusing to run: a live "
            f"job labelled as paper will not raise a real alarm. Fix the "
            f"declaration, or the script."
        )
    return declared


# --------------------------------------------------------------------------
# The four-condition CRITICAL test (from a recent incident review)
# --------------------------------------------------------------------------
CRITICAL_DOCTRINE = """\
A run may be CRITICAL only when ALL FOUR are true:

  1. Real money is at risk RIGHT NOW (money == "live", not paper, not a
     backtest, not a closed book).
  2. An unattended position or open order actually exists.
  3. Automated protection has stopped working.
  4. Only the operator can fix it. If the runner or an agent can fix it, it is not
     critical, it is a repair task.

If you are about to set critical = true, read those four again. In a recent
incident nine jobs carried the flag and the honest count was two. A red stop sign
that is wrong is worse than no stop sign, because it trains the reader to ignore
the next one.
"""


@dataclass
class Sentinel:
    """A parsed @@JOBRUN_RESULT@@ line. Enrichment only, never authoritative."""

    outcome: str | None = None
    reason_code: str | None = None
    summary: str | None = None
    metrics: dict = field(default_factory=dict)
    malformed: bool = False
    raw: str | None = None


def parse_sentinel(stdout: str, sanitize=None) -> Sentinel | None:
    """
    Extract the FINAL complete sentinel from stdout.

    Only the last one counts: a job that retries internally may emit several,
    and the last reflects its final state. A malformed sentinel returns a
    Sentinel with malformed=True rather than None, because "the job tried to
    tell us something and garbled it" is a different fact from "the job said
    nothing", and the first deserves a marker in the ledger.

    ``sanitize`` is a callable applied to every free-text field before it is
    stored. THE BUG THIS FIXES: the summary was copied straight into the
    incident card, which is printed to stdout AND sent to notify_target. A job
    that put a token or connection string in its own summary would leak it past
    the runner's redaction, which only covered raw stdout/stderr. Free text
    from a child process is untrusted and must be scrubbed and bounded.
    """
    if not stdout or SENTINEL_PREFIX not in stdout:
        return None
    found = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith(SENTINEL_PREFIX):
            continue
        found = line
    if found is None:
        return None

    def _clean(v, limit=300):
        if v is None:
            return None
        s = str(v)[:limit]
        return sanitize(s) if sanitize else s

    payload = found[len(SENTINEL_PREFIX):].strip()
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return Sentinel(malformed=True, raw=_clean(payload))
    if not isinstance(data, dict):
        return Sentinel(malformed=True, raw=_clean(payload))
    if data.get("schema") != SENTINEL_SCHEMA:
        return Sentinel(malformed=True, raw=_clean(payload))

    outcome = data.get("outcome")
    if outcome is not None and outcome not in LADDER:
        return Sentinel(malformed=True, raw=_clean(payload))

    metrics = data.get("metrics")
    if isinstance(metrics, dict):
        # Metrics are meant to be numbers. Anything else is free text from an
        # untrusted process and gets the same treatment as the summary.
        metrics = {
            str(k)[:64]: (v if isinstance(v, (int, float, bool))
                          else _clean(v, 120))
            for k, v in list(metrics.items())[:20]
        }
    else:
        metrics = {}

    return Sentinel(
        outcome=outcome,
        reason_code=_clean(data.get("reason_code"), 120),
        summary=_clean(data.get("summary")),
        metrics=metrics,
        raw=_clean(payload),
    )


class SpecSeverityError(Exception):
    """The spec itself is wrong about severity. Fail loudly, do not guess."""


@dataclass
class Outcome:
    """The reconciled verdict for one run."""

    severity: str
    reason_code: str
    summary: str | None = None
    metrics: dict = field(default_factory=dict)
    metadata_missing: bool = False
    clamped_from: str | None = None
    notes: list = field(default_factory=list)

    @property
    def speaks(self) -> bool:
        """Whether a human hears about this run at all."""
        return rank(self.severity) >= rank(DEGRADED)


def classify(
    *,
    state: str,
    exit_code: int | None,
    stdout: str = "",
    money: str = MONEY_NONE,
    allow_critical: bool = False,
    strict_domain_codes: bool = True,
    sanitize=None,
    exit_map: dict | None = None,
) -> Outcome:
    """
    Reconcile every severity channel into one verdict.

    PRECEDENCE, in strict order. This ordering is the whole contract:

    1. RUNNER-DERIVED TERMINATION WINS ABSOLUTELY. timeout, signal, launch
       failure, and wrapper error override any sentinel the child wrote. A job
       killed after printing a success sentinel is NOT healthy — that is the
       single most dangerous reconciliation bug available here.
    2. The child's exit code sets the MINIMUM severity.
    3. A sentinel may RAISE severity, never lower it. A job may escalate itself;
       it may never talk itself down from a nonzero exit.
    4. A missing or malformed sentinel NEVER converts failure into success.
    5. CRITICAL is clamped unless the job is permitted to reach it. The
       permission is the four-condition test, enforced by the caller via
       allow_critical.

    ``exit_map`` translates a script's OWN exit convention before step 2. It
    applies ONLY to a child_failure: a job killed by the harness or a timeout is
    a runner-derived fact, and no spec may describe its way out of it. This is
    how a script whose contract is "1 = tripwire fired" reports a fired tripwire
    as noteworthy instead of as a failure alarm.

    ``state`` is the runner's own terminal state: "success", "child_failure",
    "timeout", "signal", "wrapper_error", "skipped_overlap".
    """
    notes: list = []

    # ---- 1. Runner-derived termination overrides everything -------------
    if state in ("timeout", "signal", "wrapper_error"):
        sev = {
            "timeout": DEGRADED,
            "signal": DEGRADED,
            "wrapper_error": DEGRADED,
        }[state]
        sent = parse_sentinel(stdout, sanitize)
        if sent and sent.outcome and rank(sent.outcome) > rank(sev):
            # A sentinel may still RAISE. A job that printed "critical" and then
            # timed out is at least as bad as the timeout suggests.
            sev = sent.outcome
        if sent and sent.outcome and rank(sent.outcome) < rank(sev):
            notes.append(
                f"sentinel claimed {sent.outcome!r}; overridden by {state}"
            )
        out = Outcome(
            severity=sev,
            reason_code=state,
            summary=(sent.summary if sent else None),
            metrics=(sent.metrics if sent else {}),
            metadata_missing=(sent is None or sent.malformed),
            notes=notes,
        )
        return _clamp_critical(out, allow_critical, money)

    if state == "skipped_overlap":
        return Outcome(
            severity=NOTEWORTHY,
            reason_code="skipped_overlap",
            summary="previous run still holding the lock",
        )

    # ---- 2. Exit code sets the floor ------------------------------------
    code = 0 if exit_code is None else int(exit_code)

    # A per-job exit_map translates the script's own convention BEFORE any
    # reserved-code check. It applies only to a real child exit: runner-derived
    # terminations were already returned above and cannot be remapped.
    mapped = None
    if exit_map and state == "child_failure" and code in exit_map:
        mapped = exit_map[code]

    if mapped is not None:
        floor = mapped
        reason = f"exit_{code}_mapped_{mapped}"
        notes.append(
            f"exit {code} mapped to {mapped} by the job's declared convention")
    elif strict_domain_codes and code in FORBIDDEN_DOMAIN_CODES:
        raise SpecSeverityError(
            f"child exited {code}, which is RESERVED. Codes 3-9 must never "
            f"carry domain meaning: a job once encoded a research state as "
            f"exit 3 and read as 12 failures while working "
            f"correctly. Use 0/10/20/30 and put domain state in the "
            f"sentinel's reason_code."
        )
    elif code in CHILD_EXIT_SEVERITY:
        floor = CHILD_EXIT_SEVERITY[code]
        reason = f"exit_{code}"
    elif code == 0:
        floor, reason = HEALTHY, "exit_0"
    else:
        # Unknown nonzero: legacy scripts, uncaught exceptions, 127, 128+N.
        # Treated as DEGRADED, never silently healthy, never auto-critical.
        floor, reason = DEGRADED, "failed_unknown"
        notes.append(f"unclassified exit code {code}")

    # ---- 3/4. Sentinel may raise, never lower ---------------------------
    sent = parse_sentinel(stdout, sanitize)
    severity = floor
    summary = None
    metrics: dict = {}
    metadata_missing = False

    if sent is None:
        # Legacy script with no sentinel. Exit code stands on its own.
        metadata_missing = floor != HEALTHY
    elif sent.malformed:
        metadata_missing = True
        notes.append("sentinel present but malformed")
    else:
        summary = sent.summary
        metrics = sent.metrics
        if sent.reason_code:
            reason = sent.reason_code
        if sent.outcome:
            if rank(sent.outcome) > rank(floor):
                severity = sent.outcome
            elif rank(sent.outcome) < rank(floor):
                notes.append(
                    f"sentinel claimed {sent.outcome!r}; exit code {code} "
                    f"holds the floor at {floor!r}"
                )

    out = Outcome(
        severity=severity,
        reason_code=reason,
        summary=summary,
        metrics=metrics,
        metadata_missing=metadata_missing,
        notes=notes,
    )
    return _clamp_critical(out, allow_critical, money)


def _clamp_critical(out: Outcome, allow_critical: bool, money: str) -> Outcome:
    """
    CRITICAL requires permission, and permission requires live money.

    ``critical = true`` in a spec no longer sets the FLOOR of every card. It
    raises the CEILING a job is permitted to reach. A report generator that
    exits 30 is clamped to DEGRADED and the clamp is recorded as a spec bug.

    Condition 1 of the four-condition test is checked here mechanically: no
    live money, no CRITICAL. That alone would have caught the paper desk.
    """
    if out.severity != CRITICAL:
        return out
    if not allow_critical:
        out.clamped_from = CRITICAL
        out.severity = DEGRADED
        out.notes.append(
            "SPEC BUG: job emitted CRITICAL but is not permitted to. "
            "Set critical = true only if the four-condition test passes."
        )
        return out
    if money != MONEY_LIVE:
        out.clamped_from = CRITICAL
        out.severity = DEGRADED
        out.notes.append(
            f"SPEC BUG: CRITICAL requires money=live, this job is "
            f"{money!r}. Condition 1 of the four-condition test fails."
        )
    return out


# --------------------------------------------------------------------------
# Dedup — the fix for "many identical red cards, one condition"
# --------------------------------------------------------------------------
_STACK_NOISE = [
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "TS"),
    (re.compile(r"\bline \d+"), "line N"),
    # A measured age moves every run while the condition does not. Without
    # this, `quote is 0.1h old`, then `0.2h old`, fingerprints as two incidents
    # and a five-minute guard bypasses dedup indefinitely. Keep the configured
    # limit in the basis; normalize only the changing value before `old`.
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|m|h|d)\b(?=\s*old\b)"), "DUR"),
    (re.compile(r"\b\d{5,}\b"), "N"),
    (re.compile(r"/tmp/[^\s'\"]+"), "/tmp/PATH"),
    (re.compile(r"run_id[=: ]+\S+"), "run_id=RID"),
]


def normalize_error(text: str, limit: int = 400) -> str:
    """
    Strip run-varying noise so the same bug fingerprints identically.

    Without this, timestamps and addresses make every occurrence unique and
    dedup silently does nothing — which is indistinguishable from not having
    built it.
    """
    if not text:
        return ""
    s = text.strip()[-limit:]
    for pat, repl in _STACK_NOISE:
        s = pat.sub(repl, s)
    return " ".join(s.split())


def fingerprint(
    *,
    host: str,
    job_id: str,
    reason_code: str,
    error_text: str = "",
    deployed_sha: str | None = None,
) -> str:
    """
    Stable identity for ONE CONDITION, not one run.

    Includes deployed_sha deliberately: after a fix ships, the same symptom is
    a NEW incident with a fresh repair budget. Without the SHA a fixed bug looks
    like the same exhausted incident forever and never gets another attempt.
    """
    basis = "|".join([
        host,
        job_id,
        reason_code,
        normalize_error(error_text, limit=200),
        deployed_sha or "nosha",
    ])
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Repair eligibility — the gate that would have stopped the timeout flood
# --------------------------------------------------------------------------
# LLM repair is for REPRODUCIBLE CODE DEFECTS only. Everything below is a
# condition an agent cannot fix by editing source, and dispatching one is spend
# with no possible benefit.
#
# Seen in practice: a watchdog's timeout failures were an upstream outage while
# the job itself was healthy. Under this gate, zero of them reach an agent. When a
# timeout IS the job's own fault the fix is a spec change, which the runner should
# SAY rather than send a model at.
NON_REPAIRABLE = {
    "timeout": "job exceeded its own timeout — usually a spec or capacity "
               "problem, not a code defect. Check timeout vs schedule interval.",
    "signal": "killed by a signal — host pressure or an operator, not a bug.",
    "skipped_overlap": "previous run still running — a cadence problem.",
    "auth": "authentication or permission failure — needs a credential, "
            "not a patch.",
    "quota": "quota or rate limit — needs backoff or a plan change.",
    "network": "upstream or dependency outage — not our code.",
    "missing_secret": "a secret is absent from the environment.",
    "host": "host-level failure.",
}

_CLASSIFY_PATTERNS = [
    ("auth", re.compile(
        r"\b(401|403|unauthorized|forbidden|invalid[_ ]api[_ ]key|"
        r"authentication fail|permission denied)\b", re.I)),
    ("quota", re.compile(
        r"\b(429|rate.?limit|quota exceeded|too many requests|"
        r"insufficient[_ ]quota)\b", re.I)),
    ("network", re.compile(
        r"\b(connection (refused|reset|timed out)|dns|temporary failure in "
        r"name resolution|ssl|econnreset|unreachable|502|503|504)\b", re.I)),
    ("missing_secret", re.compile(
        r"\b(missing .{0,20}(credential|secret|token|key)|"
        r"environment variable .{0,30} not set)\b", re.I)),
]


def failure_class(reason_code: str, error_text: str = "") -> str:
    """Classify a failure into a repairable/non-repairable bucket."""
    if reason_code in NON_REPAIRABLE:
        return reason_code
    for name, pat in _CLASSIFY_PATTERNS:
        if pat.search(error_text or ""):
            return name
    return "code_defect"


def repair_eligible(
    *,
    reason_code: str,
    error_text: str = "",
    money: str = MONEY_NONE,
    severity: str = DEGRADED,
) -> tuple[bool, str]:
    """
    Decide whether an LLM repair agent may be dispatched.

    Returns (eligible, human_readable_reason). The reason is surfaced in the
    incident card either way, so a suppressed dispatch is never silent.
    """
    if severity == CRITICAL:
        return False, (
            "CRITICAL runs page a human. An agent does not get to rewrite the "
            "thing standing between the operator and a loss while it is failing."
        )
    if money == MONEY_LIVE:
        return False, (
            "job touches live money — repair is proposed to a human, never "
            "dispatched automatically."
        )
    cls = failure_class(reason_code, error_text)
    if cls in NON_REPAIRABLE:
        return False, NON_REPAIRABLE[cls]
    return True, "reproducible code defect"


# --------------------------------------------------------------------------
# Rendering — honest cards
# --------------------------------------------------------------------------
_GLYPH = {
    HEALTHY: "",
    NOTEWORTHY: "·",
    DEGRADED: "⚠️",
    CRITICAL: "🛑",
}


def render_card(
    *,
    outcome: Outcome,
    job_id: str,
    host: str,
    money: str,
    occurrence_count: int = 1,
    first_seen_at: str | None = None,
    duration_s: float | None = None,
    deployed_sha: str | None = None,
    owner: str | None = None,
    log_path: str | None = None,
    run_id: str | None = None,
    repair_note: str | None = None,
) -> str:
    """
    One incident card per CONDITION, carrying its occurrence count.

    The money line is derived from the reconciled money class, NEVER from a
    hand-set flag. v1 printed "This job moves real money" on a paper desk
    because one boolean decided it.
    """
    glyph = _GLYPH.get(outcome.severity, "⚠️")
    head = f"{glyph} {outcome.severity.upper()} — {job_id}"
    if outcome.summary:
        head += f": {outcome.summary}"
    else:
        head += f" ({outcome.reason_code})"
    lines = [head.strip()]

    if occurrence_count > 1:
        # An identical repeated alert is an UNACKNOWLEDGED ALARM, not
        # redundancy. Attach the count and the age; never post a fresh copy.
        since = f" since {first_seen_at}" if first_seen_at else ""
        lines.append(f"Occurrence {occurrence_count}{since} — same condition.")

    meta = [f"Host: {host}"]
    if duration_s is not None:
        meta.append(f"Duration: {duration_s:.1f}s")
    lines.append("  ·  ".join(meta))

    if money == MONEY_LIVE:
        lines.append("This job touches LIVE money. Verify state before rerunning.")
    elif money == MONEY_PAPER:
        lines.append("Paper account — no real capital at risk.")

    if outcome.clamped_from:
        lines.append(
            f"NOTE: emitted {outcome.clamped_from} but was clamped. "
            f"{outcome.notes[-1] if outcome.notes else ''}".strip()
        )
    if outcome.metadata_missing:
        lines.append("(no structured result; classified from exit code alone)")
    if repair_note:
        lines.append(f"Repair: {repair_note}")
    if deployed_sha:
        lines.append(f"Code: {deployed_sha}")
    if owner:
        lines.append(f"Owner: {owner}")
    if log_path:
        lines.append(f"Log: {log_path}")
    if run_id:
        lines.append(f"Run: {run_id[:8]}")
    return "\n".join(lines)
