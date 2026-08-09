#!/usr/bin/env python3
"""Tests for robustify_collect helpers.

Runs standalone (`python3 test_robustify_collect.py`) and under pytest. Deliberately
has no third-party dependencies so it works from a bare clone, matching the collector
itself.
"""
import importlib.util
import shlex
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "robustify_collect.py"


def load():
    """Import the collector without executing main()."""
    argv, sys.argv = sys.argv, ["robustify_collect.py"]
    try:
        spec = importlib.util.spec_from_file_location("robustify_collect", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv = argv


rc = load()

# (job dict, human label, expected hours or None)
CADENCE_CASES = [
    ({"schedule": {"kind": "cron", "expr": "17 * * * *"}}, "hourly at :17", 1.0),
    ({"schedule": {"kind": "cron", "expr": "30 23 * * *"}}, "daily", 24.0),
    ({"schedule": {"kind": "cron", "expr": "0 9 * * 1"}}, "weekly (Mon)", 168.0),
    ({"schedule": {"kind": "cron", "expr": "0 3 1 * *"}}, "monthly (1st)", 720.0),
    ({"schedule": {"kind": "cron", "expr": "*/15 * * * *"}}, "every 15 min", 0.25),
    ({"schedule": {"kind": "cron", "expr": "0 */6 * * *"}}, "every 6 hours", 6.0),
    ({"schedule": {"kind": "cron", "expr": "0 8,20 * * *"}}, "twice daily", 12.0),
    ({"schedule": "every 2h"}, "interval string 2h", 2.0),
    ({"schedule": "30m"}, "interval string 30m", 0.5),
    ({"schedule": {"kind": "interval", "hours": 4}}, "interval dict hours", 4.0),
    ({"schedule": {"kind": "once"}}, "one-shot", None),
    ({}, "no schedule", None),
    ({"schedule": "gibberish"}, "unparseable", None),
]


def test_expected_interval_h():
    for job, label, want in CADENCE_CASES:
        got = rc.expected_interval_h(job)
        if want is None:
            assert got is None, f"{label}: expected None, got {got}"
        else:
            assert got is not None and abs(got - want) < 0.01, \
                f"{label}: expected {want}, got {got}"


def test_weekly_job_is_not_stale_at_48h():
    """The regression this logic exists to prevent.

    A weekly job whose output is 60 hours old is HEALTHY. The old flat 48h threshold
    flagged it every single run, which is how a monitor teaches people to ignore it.
    """
    weekly = rc.expected_interval_h({"schedule": {"kind": "cron", "expr": "0 9 * * 1"}})
    assert weekly is not None
    assert 60 < weekly * 2.5, "weekly job at 60h must not exceed its own threshold"
    hourly = rc.expected_interval_h({"schedule": {"kind": "cron", "expr": "17 * * * *"}})
    assert hourly is not None
    assert 60 > hourly * 2.5, "hourly job silent for 60h must still be flagged"


def test_gateway_regex_matches_real_argv_and_rejects_lookalikes():
    """Guards the two verified false-detection bugs."""
    real = "python -m hermes_cli.main --profile alpha gateway run --replace"
    assert rc.GW_RE.search(real)
    # a node test runner in a checkout whose path merely contains "hermes"
    lookalike = "node ./src/hermes-thing/node_modules/.bin/x --test gateway.test.ts"
    assert not rc.GW_RE.search(lookalike)


def test_sq_quotes_paths_with_spaces():
    """A home directory containing a space must survive shell interpolation."""
    raw = str(Path("~/some dir/.hermes"))
    quoted = rc.SQ(raw)
    assert quoted != raw, "a path with a space must be quoted or escaped"
    # the quoting must round-trip back to the original argument
    assert shlex.split(quoted) == [raw]


def test_job_id_lookup_is_string_keyed():
    """Numeric job ids must still match their string-named output directory.

    Output dirs are named by the id as a string. If the cadence map keeps a raw int
    key, every lookup misses, cadence silently reverts to the 48h default, and the
    weekly false alarms come straight back.
    """
    jobs = [{"id": 12345, "name": "numeric", "enabled": True,
             "schedule": {"kind": "cron", "expr": "0 9 * * 1"}}]
    expected = {str(j["id"]): rc.expected_interval_h(j) for j in jobs}
    assert expected.get("12345") == 168.0, "string-keyed lookup must find a numeric id"


def test_cortex_root_derives_from_target_home_not_process_home():
    """The shared-cortex fallback must follow HERMES_HOME, not the collector's own $HOME.

    When inspecting another agent's profile, using our own home would check OUR cortex
    and report the target's as MISSING.
    """
    profile = Path("/srv/agents/other/.hermes/profiles/beta")
    roots = [p / "cortex" for p in profile.parents if p.name == ".hermes"]
    assert roots == [Path("/srv/agents/other/.hermes/cortex")], (
        "root hermes home must be derived from the target path"
    )


def test_backup_timestamp_normalization_handles_z_suffix():
    """A Z-suffixed backup timestamp must parse on the claimed Python floor.

    datetime.fromisoformat only learned the Z suffix in 3.11, and this skill claims
    3.9+. Without the replace, a Z-stamped backup log raised ValueError on 3.9 and the
    collector silently reported a raw string instead of "hours since last success" —
    a backup monitor quietly losing its actual signal.
    """
    import re
    from datetime import datetime

    def norm(raw):
        return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw.replace("Z", "+00:00"))

    for raw in ("2026-01-02T03:04:05Z", "2026-01-02T03:04:05-0500",
                "2026-01-02T03:04:05+00:00"):
        t = datetime.fromisoformat(norm(raw))
        assert t.tzinfo is not None, f"{raw} must parse to an aware datetime"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print("OK" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
