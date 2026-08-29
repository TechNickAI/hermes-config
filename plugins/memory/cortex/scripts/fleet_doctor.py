#!/usr/bin/env python3
"""Run the Cortex nightly doctor across every profile on every host.

Wraps ``nightly_doctor.py`` (which checks ONE store) into a fleet sweep, and
prints only what a human needs to act on.

Targets are NOT hardcoded: they come from a JSON file naming each profile, its
host, its interpreter and its Hermes home. That keeps machine names, usernames
and absolute home paths out of this repository, and lets a fleet of any shape
use the same code. See ``fleet_doctor_targets.example.json``.

    python fleet_doctor.py --targets ~/.hermes/cortex_fleet_targets.json

Output contract (cron runs this with ``no_agent: true``, so stdout IS the
message that reaches a human):

  * everything healthy, nothing repaired -> print NOTHING (silent success)
  * self-repaired                        -> keep repair facts in the run log,
                                            but do not interrupt the owner
  * still broken / setup failure         -> alarm with the reason
  * unreachable / unparseable            -> warn as inconclusive, never "down"

Exit status is always 0. The body carries the signal; a non-zero exit would
make the scheduler emit a second, redundant failure alert.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 2400
DEFAULT_RETRY_DELAY = 20
DEFAULT_QUERY = "memory"

# States worth a second look. A durable fault (corruption, missing embeddings)
# is a fact and is reported immediately; only these can be a passing blip.
SOFT_STATES = ("broken", "unreachable", "indeterminate")

# Substrings marking a fault as durable rather than transient. Matched against
# the assembled detail string.
DURABLE_MARKERS = (
    "FTS still corrupt",
    "sqlite integrity",
    "unembedded",
    "foreign embed model",
    "dimension drift",
)


class Target:
    """One profile to check: where it lives and how to run Python there."""

    __slots__ = ("label", "host", "python", "doctor", "home")

    def __init__(self, label: str, python: str, doctor: str, home: str, host: str | None = None):
        self.label = label
        self.host = host  # None means local
        self.python = python
        self.doctor = doctor
        self.home = home

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Target":
        missing = [k for k in ("label", "python", "doctor", "home") if not raw.get(k)]
        if missing:
            raise ValueError(f"target {raw.get('label', '<unnamed>')} missing keys: {missing}")
        host = raw.get("host") or None

        def resolve(value: str) -> str:
            # Expand ~ locally only. For a remote target the path belongs to
            # THAT machine, so expanding it here would substitute the wrong
            # home; ssh's shell expands it on arrival instead.
            return str(Path(value).expanduser()) if host is None else value

        return cls(
            label=raw["label"],
            python=resolve(raw["python"]),
            doctor=resolve(raw["doctor"]),
            home=resolve(raw["home"]),
            host=host,
        )


def _remote_arg(value: str) -> str:
    """Quote one argument for the remote shell, preserving a leading ``~``.

    ``shlex.quote('~/x')`` yields ``'~/x'``, which the remote shell treats as a
    literal directory named ``~`` -- verified over ssh. So quote the remainder
    and leave the tilde bare, which is what lets ``~`` in a targets file expand
    to the REMOTE user's home.
    """
    if value.startswith("~/"):
        rest = value[2:]
        return "~/" + shlex.quote(rest) if rest else "~/"
    return shlex.quote(value)


def load_targets(path: Path) -> list[Target]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        if "targets" not in data:
            # A misspelled or omitted key must be a clean configuration error,
            # not a KeyError traceback that breaks the exit-zero contract.
            raise ValueError(
                f"{path} has no 'targets' key (found: {sorted(data)[:5]})"
            )
        raw = data["targets"]
    else:
        raw = data
    if not raw:
        raise ValueError(f"no targets defined in {path}")
    return [Target.from_dict(item) for item in raw]


def probe(target: Target, query: str, timeout: int) -> dict[str, Any]:
    """Run the doctor for one profile and classify the outcome."""
    argv = [
        target.python, target.doctor,
        "--profile-home", target.home,
        "--query", query,
        "--repair",
    ]
    cmd = argv if target.host is None else [
        # ssh hands the remote command to a shell, so each argument must be
        # quoted or a path/query containing spaces silently splits apart.
        "ssh", "-o", "ConnectTimeout=30", target.host,
        " ".join(_remote_arg(part) for part in argv),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"label": target.label, "state": "unreachable", "retryable": True,
                "detail": f"timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001 - any launch failure is inconclusive
        return {"label": target.label, "state": "unreachable", "retryable": True,
                "detail": str(exc)[:200]}

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        # No parseable result means we cannot tell healthy from broken. Say
        # exactly that rather than guessing in either direction.
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        return {"label": target.label, "state": "indeterminate", "retryable": True,
                "detail": (tail[-1][:200] if tail else f"no output, rc={proc.returncode}")}

    if data.get("setup_error"):
        # Setup failures are configuration facts, not blips: report immediately.
        return {"label": target.label, "state": "setup", "retryable": False,
                "detail": str(data["setup_error"])[:220]}

    if data.get("ok"):
        repairs = data.get("repairs") or []
        if not repairs:
            return {"label": target.label, "state": "healthy"}
        emb = data.get("embeddings_after") or {}
        return {"label": target.label, "state": "repaired",
                "detail": f"{', '.join(repairs)} "
                          f"({emb.get('embedded')}/{emb.get('pages')} embedded)"}

    return {"label": target.label, "state": "broken", **_explain(data)}


def _explain(data: dict[str, Any]) -> dict[str, Any]:
    """Turn a failing doctor result into a human reason plus a retry decision."""
    reasons: list[str] = []

    if not (data.get("fts_after") or {}).get("ok"):
        reasons.append("FTS still corrupt")

    emb = data.get("embeddings_after") or {}
    if not emb.get("ok"):
        if emb.get("missing"):
            reasons.append(f"{emb['missing']} pages unembedded")
        if emb.get("foreign_model_rows"):
            reasons.append(f"foreign embed model {emb['foreign_model_rows']}")
        if emb.get("dimension_drift"):
            reasons.append("embedding dimension drift")

    retrieval = data.get("retrieval") or {}
    if not retrieval.get("ok"):
        sources = sorted(set(retrieval.get("sources") or []))
        reasons.append(f"retrieval degraded (sources={sources})")

    if data.get("sqlite_integrity_after") not in (None, "ok"):
        reasons.append("sqlite integrity failed")

    if not data.get("embedding_endpoint_healthy"):
        reasons.append("embedding endpoint unreachable")

    detail = "; ".join(reasons) or "unknown"
    durable = any(marker in detail for marker in DURABLE_MARKERS)
    return {"detail": detail, "retryable": not durable}


def check(target: Target, query: str, timeout: int, retry_delay: int) -> dict[str, Any]:
    """Probe once; retry soft failures so a blip does not wake anyone at 4am.

    Observed live: one run reported lexical-only retrieval and the next eight
    were clean. Corruption and setup errors are durable and never retried.
    """
    result = probe(target, query, timeout)
    if result["state"] in SOFT_STATES and result.get("retryable"):
        time.sleep(retry_delay)
        return probe(target, query, timeout)
    return result


def format_report(results: list[dict[str, Any]], *, audit_repairs: bool = True) -> str:
    """Render owner-facing stdout; optionally retain repair facts on stderr."""
    broken = [r for r in results if r["state"] in ("broken", "setup")]
    odd = [r for r in results if r["state"] in ("unreachable", "indeterminate")]
    repaired = [r for r in results if r["state"] == "repaired"]
    healthy = [r for r in results if r["state"] == "healthy"]

    lines: list[str] = []

    if broken:
        lines.append(f"🔴 Cortex unhealthy on {len(broken)} of {len(results)} profiles")
        lines += [f"  {r['label']}: {r['detail']}" for r in broken]

    if odd:
        if not lines:
            lines.append(f"⚠️ Cortex check inconclusive on {len(odd)} of {len(results)} profiles")
        lines += [f"  {r['label']} ({r['state']}): {r['detail']}" for r in odd]

    # A repair-only run restored the invariant and leaves no owner action. Its
    # exact facts already remain in the jobrun log, so delivering them every
    # night is success narration. Mixed reports still include repair context
    # beneath a real broken/inconclusive headline.
    if repaired:
        repair_lines = [f"  {r['label']}: {r['detail']}" for r in repaired]
        if lines:
            lines += repair_lines
        elif audit_repairs:
            print("Cortex self-repaired overnight", file=sys.stderr)
            print("\n".join(repair_lines), file=sys.stderr)

    # Blast radius: an alarm should say what is still fine, not just what broke.
    if lines and (broken or odd):
        lines.append(f"  still healthy: {len(healthy)} of {len(results)}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Cortex nightly doctor fleet-wide.")
    parser.add_argument("--targets", required=True,
                        help="JSON file describing the profiles to check")
    parser.add_argument("--query", default=DEFAULT_QUERY,
                        help="retrieval canary query (default: %(default)s)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retry-delay", type=int, default=DEFAULT_RETRY_DELAY)
    parser.add_argument("--max-workers", type=int, default=6)
    args = parser.parse_args(argv)

    try:
        targets = load_targets(Path(args.targets).expanduser())
    except Exception as exc:  # noqa: BLE001
        # Catch-all on purpose: silence means "all clear", so ANY failure to
        # load targets must speak up rather than exit with a traceback.
        print(f"🔴 Cortex fleet doctor could not load targets: {exc}")
        return 0

    with cf.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        results = list(pool.map(
            lambda t: check(t, args.query, args.timeout, args.retry_delay), targets
        ))

    report = format_report(results)
    if report:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
