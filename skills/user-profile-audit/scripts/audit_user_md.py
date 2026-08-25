#!/usr/bin/env python3
"""Audit a Hermes USER.md against the user-profile-audit rubric.

Read-only. Never writes memory. Emits findings as evidence for human adjudication.

Usage:
    audit_user_md.py [PROFILE_HOME ...]   # defaults to $HERMES_HOME or ~/.hermes
    audit_user_md.py --json               # machine-readable

Exit codes: 0 = no HIGH findings, 1 = at least one HIGH finding, 2 = nothing audited,
3 = a target was unreadable/nonexistent so the run is INCOMPLETE.
NOTE: exit 1 means "found something", NOT "the script failed".

PRIVACY: SECRET evidence is redacted in EVERY output mode and is never printed. The
audit report is itself sensitive - it names which profile holds what - so treat its
output, and any file you redirect it into, as personal data. There is deliberately no
flag to print raw secret values: the adjudicator reads the source file directly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_MEMORY_CAP = 2200
DEFAULT_USER_CAP = 1375
ENTRY_DELIM = "\n\u00a7\n"
NEAR_CAP_RATIO = 0.85
FAT_ENTRY_CHARS = 600
MONOLITH_MIN_CHARS = 400

Sev = str
HIGH, MED, LOW = "HIGH", "MED", "LOW"
_ORDER = {HIGH: 0, MED: 1, LOW: 2}

# --- Detectors ------------------------------------------------------------
# Patterns describe the CLASS to find. Never hardcode known-bad literals: an
# audit that only finds strings you already knew about finds nothing new.

SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:sk-|pk_|ghp_|gho_|ghs_|xox[baprs]-|AKIA|ASIA)[A-Za-z0-9_\-]{8,}", "credential token"),
    (r"(?i)\b(?:api[_ -]?key|secret|password|passwd|passphrase|token|bearer)\b\s*(?:[:=]|\bis\b)?\s*\S{6,}", "key/value secret"),
    (r"(?i)\b(?:accounts?|acct|checking|savings|routing)\b[^.\n]{0,40}\b\d{4,17}\b(?:\s*[/,]\s*\d{4,17})*", "account number fragment"),
    (r"(?<!\d)(?:\d[ -]?){13,}", "card/account-length digit run"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN-shaped value"),
    (r"(?i)\b(?:routing|iban|swift)\b(?:\s+(?:number|num|no|code|#))?\s*[:=#]?\s*(?=\S*\d)[A-Z0-9]{6,}", "bank identifier"),
]

PII_PATTERNS: list[tuple[str, str]] = [
    (
        r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+"
        r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Way|Ct|Court|Pl|Place)\b",
        "street address",
    ),
    (r"(?<!\w)\+?1?[\s.\-]?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\w)", "phone number"),
    (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "email address"),
]

TRANSIENT_PATTERNS: list[tuple[str, str]] = [
    (r"\b20\d{2}-\d{2}-\d{2}\b", "hard date"),
    (r"(?i)(?:^|[^\w])(?:currently|right now|as of|this week|today|at the moment|for now)\b", "point-in-time qualifier"),
    (r"(?i)\b(?:PR|issue)\s*#?\d+\b", "PR/issue number"),
    (r"(?i)\b(?:in progress|pending|blocked on|waiting on|next step)\b", "task state"),
]

MISFILED_PATTERNS: list[tuple[str, str]] = [
    (r"(?:/Users/|/home/|~/\.)[\w./\-]*", "filesystem path"),
    (r"(?i)\b(?:systemctl|crontab|ssh |venv|pip install|npm |docker )\S*", "shell command"),
    (r"(?i)\b(?:localhost|127\.0\.0\.1|:\d{4,5}\b)", "host/port"),
    (r"(?i)\b(?:repo|branch|deploy|endpoint|schema|migration)\b", "project mechanics"),
]

IMPERATIVE_RE = re.compile(
    r"(?im)^\s*(?:always|never|do not|don't|make sure|ensure|remember to|you must|use )\b"
)


def entries_of(text: str) -> list[str]:
    return [e.strip() for e in text.split(ENTRY_DELIM) if e.strip()]


def _int_or(value, default: int) -> int:
    """Coerce a configured cap, falling back when it is null/blank/non-numeric.

    A hand-edited or commented-out cap must not take down a whole fleet run with an
    uncaught ValueError/TypeError.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_cap(profile_home: Path) -> tuple[int, int, bool, bool, str | None]:
    """Return (user_cap, memory_cap, user_overridden, memory_overridden, config_note)."""
    cfg_path = profile_home / "config.yaml"
    mem: dict = {}
    note: str | None = None
    if cfg_path.exists():
        try:
            import yaml  # noqa: PLC0415

            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            section = loaded.get("memory")
            if isinstance(section, dict):
                mem = section
        except ImportError:
            note = "PyYAML unavailable - caps fall back to documented defaults"
        except Exception as exc:
            # Silently scoring against defaults would report a false OVER_CAP on a
            # profile whose real cap is raised. Surface it instead.
            note = f"config.yaml present but unreadable ({type(exc).__name__}) - caps are defaults"
    user_cap = _int_or(mem.get("user_char_limit", DEFAULT_USER_CAP), DEFAULT_USER_CAP)
    memory_cap = _int_or(mem.get("memory_char_limit", DEFAULT_MEMORY_CAP), DEFAULT_MEMORY_CAP)
    return user_cap, memory_cap, "user_char_limit" in mem, "memory_char_limit" in mem, note


def scan(text: str, user_cap: int) -> list[dict]:
    findings: list[dict] = []

    def add(sev: Sev, code: str, msg: str, evidence: str = "") -> None:
        findings.append({"severity": sev, "code": code, "message": msg, "evidence": evidence})

    n = len(text)
    ents = entries_of(text)

    if user_cap > 0:
        if n > user_cap:
            add(HIGH, "OVER_CAP", f"{n} chars exceeds effective cap {user_cap}")
        elif n >= user_cap * NEAR_CAP_RATIO:
            add(MED, "NEAR_CAP", f"{n}/{user_cap} chars ({n / user_cap:.0%}) - overflow pressure")

    if len(ents) == 1 and n > MONOLITH_MIN_CHARS:
        add(MED, "MONOLITH", f"whole file is a single {n}-char entry; not surgically editable")

    for e in ents:
        if len(e) > FAT_ENTRY_CHARS:
            add(LOW, "FAT_ENTRY", f"entry of {len(e)} chars welds concerns together", e[:60] + "...")

    for patterns, sev, code in (
        (SECRET_PATTERNS, HIGH, "SECRET"),
        (PII_PATTERNS, MED, "PII"),
        (TRANSIENT_PATTERNS, MED, "TRANSIENT"),
        (MISFILED_PATTERNS, LOW, "MISFILED"),
    ):
        for rx, label in patterns:
            for m in re.finditer(rx, text):
                add(sev, code, label, m.group(0).strip()[:70])

    for m in IMPERATIVE_RE.finditer(text):
        add(LOW, "IMPERATIVE", "phrased as an instruction, not a declarative fact", m.group(0).strip())

    return findings


def _contains_secret(value: str) -> bool:
    """True if this evidence string contains anything a SECRET pattern would match.

    Redaction cannot be keyed on the finding's own code: a credential sitting inside a
    600-char entry is captured verbatim by FAT_ENTRY, and a password that happens to be
    an email address is captured by PII. Both then printed in the clear. Every piece of
    evidence is checked against the secret patterns regardless of which detector
    produced it.
    """
    return any(re.search(rx, value) for rx, _ in SECRET_PATTERNS)


def group(findings: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for f in findings:
        key = (f["code"], f["message"])
        g = grouped.setdefault(
            key, {"severity": f["severity"], "code": f["code"], "message": f["message"], "count": 0, "examples": []}
        )
        g["count"] += 1
        # SECRET evidence is redacted HERE, not at render time, so that --json and any
        # other consumer inherit the redaction. A render-time-only redaction leaks the
        # moment someone adds a new output mode.
        if f["evidence"] and len(g["examples"]) < 3:
            leaks = f["code"] == "SECRET" or _contains_secret(f["evidence"])
            g["examples"].append("<redacted>" if leaks else f["evidence"])
    return sorted(grouped.values(), key=lambda g: (_ORDER[g["severity"]], g["code"]))


def audit(profile_home: Path) -> dict | None:
    mem_dir = profile_home / "memories"
    user_file = mem_dir / "USER.md"
    if not user_file.exists():
        return None
    # CRLF would never match the "\n\u00a7\n" delimiter, collapsing every entry into
    # one and firing a spurious MONOLITH. Path.read_text() already applies universal
    # newlines, so this is belt-and-braces for any future switch to read_bytes(); the
    # CRLF test passes on either path.
    text = user_file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    user_cap, mem_cap, user_over, mem_over, cfg_note = _read_cap(profile_home)

    memory_file = mem_dir / "MEMORY.md"
    mem_chars = len(memory_file.read_text(encoding="utf-8", errors="replace")) if memory_file.exists() else None

    result = {
        "profile_home": str(profile_home),
        "user_chars": len(text),
        "user_entries": len(entries_of(text)),
        "user_cap": user_cap,
        "user_cap_overridden": user_over,
        "memory_chars": mem_chars,
        "memory_cap": mem_cap,
        "memory_cap_overridden": mem_over,
        "config_note": cfg_note,
        "findings": group(scan(text, user_cap)),
    }
    # Overflow signature: MEMORY.md at its cap while USER.md carries misfiled content.
    # "project mechanics" alone is prose-prone ("she reviews every schema change"), so
    # require a HARD misfiled signal - a real path, command, or host - before claiming
    # overflow. Otherwise a clean profile manufactures a bogus signature.
    hard_misfiled = {"filesystem path", "shell command", "host/port"}
    if mem_chars is not None and mem_cap and mem_chars >= mem_cap * NEAR_CAP_RATIO:
        if any(f["code"] == "MISFILED" and f["message"] in hard_misfiled for f in result["findings"]):
            result["findings"].append(
                {
                    "severity": MED,
                    "code": "OVERFLOW",
                    "message": (
                        f"MEMORY.md at {mem_chars}/{mem_cap} while USER.md holds misfiled "
                        "agent-note content - classic overflow-into-USER.md signature"
                    ),
                    "count": 1,
                    "examples": [],
                },
            )
            # Re-sort so a MED OVERFLOW can never display above a HIGH SECRET. The
            # append above already preserves order; the sort is what makes the
            # guarantee independent of how the finding gets added.
            result["findings"].sort(key=lambda f: (_ORDER[f["severity"]], f["code"]))
    return result


def render(result: dict) -> None:
    cap_note = " (RAISED)" if result["user_cap_overridden"] else ""
    print(f"\n{'=' * 72}")
    print(
        f"{result['profile_home']}\n  USER.md {result['user_chars']} chars / cap "
        f"{result['user_cap']}{cap_note}, {result['user_entries']} entries"
    )
    print("=" * 72)
    if not result["findings"]:
        print("  clean")
        return
    for f in result["findings"]:
        ex = ""
        if f["examples"]:
            ex = "   e.g. " + " | ".join(f["examples"])
        print(f"  [{f['severity']:4s}] {f['code']:10s} x{f['count']:<3} {f['message']}{ex}")


def discover(base: Path) -> list[Path]:
    homes = []
    if (base / "memories").is_dir():
        homes.append(base)
    profiles = base / "profiles"
    if profiles.is_dir():
        for child in sorted(profiles.iterdir()):
            if (child / "memories").is_dir():
                homes.append(child)
    return homes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("homes", nargs="*", help="profile home dirs (default: $HERMES_HOME or ~/.hermes)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.homes:
        targets: list[Path] = []
        for h in args.homes:
            expanded = Path(h).expanduser()
            if not expanded.exists():
                print(f"ERROR: path does not exist: {h}", file=sys.stderr)
                return 3
            targets.extend(discover(expanded))
    else:
        base = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
        targets = discover(base)

    # One unreadable profile must never silently drop every later profile - that is
    # the exact failure this tool exists to warn about.
    # Overlapping args (~/.hermes and ~/.hermes/profiles/x) would double-count.
    targets = list(dict.fromkeys(p.resolve() for p in targets))

    results = []
    errors = []
    for target in targets:
        try:
            r = audit(target)
        except Exception as exc:
            errors.append({"profile_home": str(target), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if r:
            results.append(r)

    for e in errors:
        print(f"ERROR reading {e['profile_home']}: {e['error']}", file=sys.stderr)

    if not results and not errors:
        print("No USER.md found in any target.", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"results": results, "errors": errors}, indent=2))
    else:
        for r in results:
            render(r)
        high = sum(1 for r in results for f in r["findings"] if f["severity"] == HIGH)
        print(f"\n{len(results)} profile(s) audited, {high} HIGH finding(s).")
        if errors:
            print(f"{len(errors)} profile(s) COULD NOT BE READ - this run is incomplete.")
        print("Findings are EVIDENCE, not verdicts - adjudicate each one.")

    # 3 outranks 1: a partial run must never be mistaken for a clean one.
    if errors:
        return 3
    return 1 if any(f["severity"] == HIGH for r in results for f in r["findings"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
