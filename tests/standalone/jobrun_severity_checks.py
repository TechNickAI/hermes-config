#!/usr/bin/env python3
"""
Contract tests for the jobrun v2 severity module.

Every contradiction case named in the design gets a test here. The research
lane's strongest counterargument to the hybrid exit-code + sentinel design was
that two truth channels create a reconciliation bug farm unless every
contradiction is handled identically everywhere. This file is the answer to
that objection; without it the design should not ship.

Run: python3 test_jobrun_severity.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'skills' / 'scheduled-job-runner' / 'scripts'))

from jobrun_severity import (  # noqa: E402
    CRITICAL,
    DEGRADED,
    HEALTHY,
    MONEY_LIVE,
    MONEY_NONE,
    MONEY_PAPER,
    NOTEWORTHY,
    SENTINEL_PREFIX,
    MoneyMismatch,
    SpecSeverityError,
    classify,
    detect_money,
    failure_class,
    fingerprint,
    normalize_error,
    parse_sentinel,
    reconcile_money,
    render_card,
    repair_eligible,
)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def sent(outcome=None, reason=None, summary=None, schema="jobrun.result/v1", raw=None):
    if raw is not None:
        return f"{SENTINEL_PREFIX} {raw}"
    import json
    d = {"schema": schema}
    if outcome:
        d["outcome"] = outcome
    if reason:
        d["reason_code"] = reason
    if summary:
        d["summary"] = summary
    return f"{SENTINEL_PREFIX} {json.dumps(d)}"


print("\n== Precedence: runner-derived termination overrides the sentinel ==")
# THE most dangerous reconciliation bug: a job killed after printing success.
o = classify(state="timeout", exit_code=None,
             stdout=sent(outcome=HEALTHY, summary="all good"))
check("timeout beats a healthy sentinel", o.severity == DEGRADED, o.severity)
check("timeout records the override", any("overridden" in n for n in o.notes))

o = classify(state="signal", exit_code=None, stdout=sent(outcome=HEALTHY))
check("signal beats a healthy sentinel", o.severity == DEGRADED, o.severity)

# A sentinel may still RAISE above a runner-derived state.
o = classify(state="timeout", exit_code=None,
             stdout=sent(outcome=CRITICAL), money=MONEY_LIVE, allow_critical=True)
check("sentinel may raise above timeout", o.severity == CRITICAL, o.severity)

print("\n== Precedence: exit code is the floor, sentinel may only raise ==")
o = classify(state="child_failure", exit_code=20, stdout=sent(outcome=HEALTHY))
check("sentinel cannot talk down a nonzero exit", o.severity == DEGRADED, o.severity)
check("downgrade attempt is noted", any("holds the floor" in n for n in o.notes))

o = classify(state="child_failure", exit_code=20,
             stdout=sent(outcome=CRITICAL), money=MONEY_LIVE, allow_critical=True)
check("sentinel may raise 20 -> critical", o.severity == CRITICAL, o.severity)

o = classify(state="success", exit_code=0, stdout=sent(outcome=DEGRADED))
check("sentinel may raise a clean exit to degraded", o.severity == DEGRADED, o.severity)

print("\n== Missing / malformed sentinel never becomes success ==")
o = classify(state="child_failure", exit_code=1)
check("legacy exit 1 -> degraded", o.severity == DEGRADED, o.severity)
check("legacy exit 1 marked unclassified", o.reason_code == "failed_unknown", o.reason_code)
check("missing metadata flagged", o.metadata_missing)

o = classify(state="child_failure", exit_code=20, stdout=f"{SENTINEL_PREFIX} {{not json")
check("malformed sentinel keeps severity", o.severity == DEGRADED, o.severity)
check("malformed sentinel flagged", o.metadata_missing)

o = classify(state="success", exit_code=0, stdout=sent(outcome="bogus_level"))
check("unknown outcome value -> malformed, stays healthy",
      o.severity == HEALTHY and o.metadata_missing, o.severity)

o = classify(state="success", exit_code=0, stdout=sent(outcome=DEGRADED, schema="other/v9"))
check("wrong schema is malformed, not obeyed", o.severity == HEALTHY, o.severity)

print("\n== Accidental sentinel-like text and multiple sentinels ==")
o = classify(state="success", exit_code=0,
             stdout="the log mentions @@JOBRUN_RESULT@@ in passing\n")
check("bare prefix without payload -> malformed not crash", o.severity == HEALTHY, o.severity)

multi = sent(outcome=DEGRADED) + "\nretrying\n" + sent(outcome=HEALTHY)
o = classify(state="success", exit_code=0, stdout=multi)
check("last sentinel wins (job recovered internally)", o.severity == HEALTHY, o.severity)

print("\n== Forbidden domain codes 3-9 (the research-state bug) ==")
raised = False
try:
    classify(state="child_failure", exit_code=3)
except SpecSeverityError:
    raised = True
check("exit 3 raises rather than silently failing", raised)

o = classify(state="child_failure", exit_code=3, strict_domain_codes=False)
check("strict=False degrades instead of raising", o.severity == DEGRADED, o.severity)

print("\n== CRITICAL clamping: permission + live money ==")
o = classify(state="child_failure", exit_code=30, money=MONEY_NONE, allow_critical=False)
check("unpermitted critical is clamped", o.severity == DEGRADED, o.severity)
check("clamp records the origin", o.clamped_from == CRITICAL)
check("clamp names it a spec bug", any("SPEC BUG" in n for n in o.notes))

# Exact regression: permitted flag, but paper money.
o = classify(state="child_failure", exit_code=30, money=MONEY_PAPER, allow_critical=True)
check("critical on a PAPER desk is clamped", o.severity == DEGRADED, o.severity)
check("clamp cites condition 1", any("condition" in n.lower() for n in o.notes))

o = classify(state="child_failure", exit_code=30, money=MONEY_LIVE, allow_critical=True)
check("critical allowed on live money", o.severity == CRITICAL, o.severity)

print("\n== Money: declared vs detected ==")
paper_sh = "#!/bin/bash\nexport ALPACA_PAPER_TRADE=true\ncurl https://api.alpaca.markets/v2/x\n"
check("paper marker wins over live host mention",
      detect_money(paper_sh) == MONEY_PAPER, detect_money(paper_sh))

live_py = "TRADING_BASE = 'https://api.alpaca.markets'\nraise SystemExit('missing Alpaca live credentials')\n"
check("live endpoint detected", detect_money(live_py) == MONEY_LIVE, detect_money(live_py))
check("plain script -> none", detect_money("print('hi')") == MONEY_NONE)

# REGRESSION from a recent incident: detection must follow a shell wrapper one
# level. A set of guard-job specs named a .sh whose only relevant line invoked a
# Python script; the live markers were in the .py. Depth-0 scanning returned
# MONEY_NONE for three jobs that touch a LIVE account — a false negative in the
# dangerous direction.
import tempfile  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="jobrun-money-"))
(_tmp / "scripts").mkdir()
(_tmp / "scripts" / "inner.py").write_text(
    "TRADING_BASE = 'https://api.alpaca.markets'\n"
    "raise SystemExit('missing Alpaca live credentials')\n"
)
_wrapper = "#!/bin/bash\ncd /somewhere || exit 0\npython3 scripts/inner.py\n"
check("wrapper alone hides live markers",
      detect_money(_wrapper) == MONEY_NONE)
check("wrapper WITH base_dir finds live markers",
      detect_money(_wrapper, _tmp) == MONEY_LIVE, detect_money(_wrapper, _tmp))

(_tmp / "scripts" / "paper_inner.py").write_text("import os\nos.environ['ALPACA_PAPER_TRADE']='true'\n")
_pw = "#!/bin/bash\npython3 scripts/paper_inner.py\n"
check("wrapper following also resolves paper",
      detect_money(_pw, _tmp) == MONEY_PAPER, detect_money(_pw, _tmp))

# Depth is capped: a wrapper calling a wrapper calling a live script stops.
(_tmp / "scripts" / "mid.sh").write_text("#!/bin/bash\npython3 scripts/inner.py\n")
_deep = "#!/bin/bash\nbash scripts/mid.sh\n"
check("depth is capped at one hop", detect_money(_deep, _tmp) == MONEY_NONE)

check("no base_dir means no filesystem walk",
      detect_money(_wrapper, None) == MONEY_NONE)

check("undeclared trusts detection", reconcile_money(None, MONEY_LIVE) == MONEY_LIVE)
check("agreement passes", reconcile_money(MONEY_PAPER, MONEY_PAPER) == MONEY_PAPER)
check("over-declaring live is allowed", reconcile_money(MONEY_LIVE, MONEY_NONE) == MONEY_LIVE)

raised = False
try:
    reconcile_money(MONEY_PAPER, MONEY_LIVE)
except MoneyMismatch:
    raised = True
check("declaring paper on a LIVE script is a hard error", raised)

raised = False
try:
    reconcile_money("sorta", MONEY_NONE)
except MoneyMismatch:
    raised = True
check("invalid money value rejected", raised)

print("\n== Repair eligibility (the timeout flood gate) ==")
ok, why = repair_eligible(reason_code="timeout")
check("timeout is NOT repairable", not ok, why)
check("timeout reason mentions the spec", "spec" in why.lower() or "cadence" in why.lower())

ok, _ = repair_eligible(reason_code="signal")
check("signal is NOT repairable", not ok)

ok, _ = repair_eligible(reason_code="failed_unknown", error_text="HTTP 429 rate limit exceeded")
check("rate limit is NOT repairable", not ok)

ok, _ = repair_eligible(reason_code="failed_unknown", error_text="401 Unauthorized")
check("auth failure is NOT repairable", not ok)

ok, _ = repair_eligible(reason_code="failed_unknown",
                        error_text="Connection refused to db:<port>")
check("network failure is NOT repairable", not ok)

ok, _ = repair_eligible(reason_code="failed_unknown",
                        error_text="TypeError: unsupported operand type(s)")
check("TypeError IS repairable", ok)

ok, _ = repair_eligible(reason_code="failed_unknown",
                        error_text="TypeError: bad", money=MONEY_LIVE)
check("live money blocks auto-repair", not ok)

ok, _ = repair_eligible(reason_code="failed_unknown", severity=CRITICAL)
check("critical blocks auto-repair", not ok)

print("\n== Fingerprint / dedup ==")
a = fingerprint(host="h", job_id="j", reason_code="failed_unknown",
                error_text="TypeError at 2026-07-14T09:30:00Z addr 0xdeadbeef line 42")
b = fingerprint(host="h", job_id="j", reason_code="failed_unknown",
                error_text="TypeError at 2026-07-14T11:15:02Z addr 0xcafef00d line 42")
check("same bug, different timestamps -> same fingerprint", a == b, f"{a} vs {b}")

c = fingerprint(host="h", job_id="j", reason_code="failed_unknown",
                error_text="TypeError at 2026-07-14T09:30:00Z", deployed_sha="abc123")
check("new deploy -> new fingerprint (fresh repair budget)", a != c)

d = fingerprint(host="h", job_id="OTHER", reason_code="failed_unknown", error_text="TypeError")
check("different job -> different fingerprint", a != d)

check("normalize strips timestamps", "TS" in normalize_error("boom at 2026-07-14T09:30:00Z"))
check("normalize strips addresses", "0xADDR" in normalize_error("at 0xdeadbeef"))

print("\n== failure_class ==")
check("timeout classed", failure_class("timeout") == "timeout")
check("clean traceback -> code_defect",
      failure_class("failed_unknown", "ZeroDivisionError") == "code_defect")

print("\n== Rendering ==")
o = classify(state="child_failure", exit_code=20,
             stdout=sent(outcome=DEGRADED, reason="sell.rejected",
                         summary="1 of 7 positions failed to sell"))
card = render_card(outcome=o, job_id="guard-job", host="trading",
                   money=MONEY_PAPER, occurrence_count=28,
                   first_seen_at="2026-08-23T15:05Z", duration_s=3.2)
check("degraded card is not a red stop sign", "🛑" not in card)
check("card carries the occurrence count", "Occurrence 28" in card, card)
check("paper account stated honestly", "Paper account" in card)
check("no false money claim on paper", "LIVE money" not in card)
check("summary surfaces the real symptom", "1 of 7 positions" in card)

o2 = classify(state="child_failure", exit_code=30, money=MONEY_LIVE, allow_critical=True)
card2 = render_card(outcome=o2, job_id="guard-job", host="studio", money=MONEY_LIVE)
check("critical card shows the stop sign", "🛑" in card2)
check("live money line only on live money", "LIVE money" in card2)

print("\n== Healthy path stays silent ==")
o = classify(state="success", exit_code=0)
check("exit 0 is healthy", o.severity == HEALTHY)
check("healthy does not speak", not o.speaks)
o = classify(state="success", exit_code=10)
check("exit 10 is noteworthy", o.severity == NOTEWORTHY)
check("noteworthy does not page", not o.speaks)
o = classify(state="child_failure", exit_code=20)
check("degraded speaks", o.speaks)

print("\n== Overlap ==")
o = classify(state="skipped_overlap", exit_code=None)
check("skipped overlap is noteworthy, not a failure", o.severity == NOTEWORTHY)
check("skipped overlap stays quiet", not o.speaks)

failed = [(n, d) for n, ok, d in _results if not ok]
print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
if failed:
    print("\nFAILURES:")
    for n, d in failed:
        print(f"  - {n} {d}")
    sys.exit(1)
print("All contract tests passed.")
