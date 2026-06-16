#!/usr/bin/env python3
"""Audit OpenClaw -> Hermes migrations for cleanup-safety gaps.

This is OUR-side migration hardening. It does not patch Hermes Agent upstream.
It inspects local OpenClaw/Hermes footprints and reports the failure classes
that made real migrations look complete while still depending on the old tree.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# Conservative subset of Hermes' runtime cron prompt scanner. Keep intentionally
# small: we only want to flag literal injection phrases that commonly appear in
# defensive boilerplate and then get BLOCKED when the cron job is recreated.
CRON_SCANNER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.I),
    re.compile(r"forget\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.I),
)

OPENCLAW_PATH_RE = re.compile(r"(?:~|\$HOME)?/?\.openclaw/[A-Za-z0-9._~/$-]+")
OPENCLAW_GENERIC_RE = re.compile(r"\.openclaw")

BLOCKER = "blocker"
WARNING = "warning"
INFO = "info"


@dataclass
class Finding:
    severity: str
    kind: str
    path: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cron_prompt_texts(job: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in ("prompt", "message"):
        value = job.get(key)
        if isinstance(value, str):
            texts.append(value)
    payload = job.get("payload")
    if isinstance(payload, dict):
        for key in ("prompt", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                texts.append(value)
    return texts


def _iter_cron_jobs(path: Path) -> Iterable[dict[str, Any]]:
    data = _load_json(path)
    if isinstance(data, dict):
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            for job in jobs:
                if isinstance(job, dict):
                    yield job
    elif isinstance(data, list):
        for job in data:
            if isinstance(job, dict):
                yield job


def audit_cron_scanner(openclaw_root: Path, hermes_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for source, path in (
        ("openclaw-cron", openclaw_root / "cron" / "jobs.json"),
        ("hermes-cron", hermes_root / "cron" / "jobs.json"),
    ):
        if not path.exists():
            continue
        for job in _iter_cron_jobs(path):
            name = str(job.get("name") or job.get("id") or "<unnamed>")
            for text in _cron_prompt_texts(job):
                for pattern in CRON_SCANNER_PATTERNS:
                    if pattern.search(text):
                        findings.append(
                            Finding(
                                severity=WARNING,
                                kind="cron-scanner-tripwire",
                                path=str(path),
                                message=(
                                    "Cron prompt contains defensive text that may match "
                                    "Hermes' runtime prompt scanner. Rephrase as inert-data "
                                    "framing before recreating/running the job."
                                ),
                                details={"source": source, "job": name, "pattern": pattern.pattern},
                            )
                        )
                        break
    return findings


def _workflow_destination(name: str, hermes_root: Path) -> str | None:
    """Return where a workflow landed in Hermes, or None if it cannot be confirmed.

    Both documented migration paths count as "ported": a lift-and-shift copy under
    workspace/workflows/, or a rewrite as a Hermes skill under either skills/ location.
    """
    if (hermes_root / "workspace" / "workflows" / name).exists():
        return "workflow"
    if (hermes_root / "skills" / name).exists() or (hermes_root / "workspace" / "skills" / name).exists():
        return "skill"
    return None


def audit_unmigrated_workflows(openclaw_root: Path, hermes_root: Path) -> list[Finding]:
    workflows_dir = openclaw_root / "workspace" / "workflows"
    if not workflows_dir.is_dir():
        return []
    source_names = sorted(
        child.name for child in workflows_dir.iterdir() if child.is_dir() and not child.name.startswith(".")
    )
    if not source_names:
        return []
    ported_as_workflow = []
    ported_as_skill = []
    missing = []
    for name in source_names:
        destination = _workflow_destination(name, hermes_root)
        if destination == "workflow":
            ported_as_workflow.append(name)
        elif destination == "skill":
            ported_as_skill.append(name)
        else:
            missing.append(name)
    return [
        Finding(
            severity=WARNING,
            kind="openclaw-workflows",
            path=str(workflows_dir),
            message=(
                "OpenClaw workflows are not migrated by Hermes itself. Review any "
                "workflows that still matter: port them into ~/.hermes/workspace, rewrite "
                "them as skills, or explicitly drop them. A workflow that now exists as a "
                "Hermes skill is already migrated. This is a cleanup blocker only when a "
                "live Hermes cron/workspace file still references the old tree."
            ),
            details={
                "source_count": len(source_names),
                "ported_as_workflow": ported_as_workflow,
                "ported_as_skill": ported_as_skill,
                "missing_from_hermes": missing,
            },
        )
    ]


def _is_ignored_workspace_path(path: Path) -> bool:
    parts = set(path.parts)
    name = path.name
    # .env files are NOT noise: a lifted-and-shifted .env often still carries
    # ~/.openclaw/... defaults that silently repoint a ported job at the old tree, so
    # they must be scanned even though they start with a dot.
    if name == ".env" or name.startswith(".env."):
        return False
    # Dot-prefixed VCS/OS cruft (.git, .DS_Store, .gitignore, ...) is noise.
    if name.startswith("."):
        return True
    if ".bak" in name or name.endswith("~"):
        return True
    if "__pycache__" in parts:
        return True
    # The compatibility shim is intentionally named openclaw and documents the
    # command it translates. It is not a dependency on the OpenClaw tree.
    if name == "openclaw" and path.parent.name == "bin":
        return True
    return False


def audit_live_openclaw_paths(hermes_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    jobs_json = hermes_root / "cron" / "jobs.json"
    if jobs_json.exists():
        text = _read_text(jobs_json)
        matches = sorted(set(OPENCLAW_PATH_RE.findall(text)))
        if matches:
            findings.append(
                Finding(
                    severity=BLOCKER,
                    kind="live-cron-openclaw-path",
                    path=str(jobs_json),
                    message="Live Hermes cron definitions still reference the OpenClaw tree.",
                    details={"matches": matches[:20], "match_count": len(matches)},
                )
            )

    workspace = hermes_root / "workspace"
    if not workspace.exists():
        return findings

    for path in sorted(p for p in workspace.rglob("*") if p.is_file()):
        if _is_ignored_workspace_path(path):
            continue
        text = _read_text(path)
        path_matches = sorted(set(OPENCLAW_PATH_RE.findall(text)))
        if path_matches:
            findings.append(
                Finding(
                    severity=BLOCKER,
                    kind="live-workspace-openclaw-path",
                    path=str(path),
                    message=(
                        "A live Hermes workspace file still references an OpenClaw path. "
                        "If this file is a prompt/AGENT.md, port any referenced secret or "
                        "asset into ~/.hermes/workspace and repoint the prose path."
                    ),
                    details={"matches": path_matches[:20], "match_count": len(path_matches)},
                )
            )
            continue
        if OPENCLAW_GENERIC_RE.search(text):
            findings.append(
                Finding(
                    severity=INFO,
                    kind="openclaw-nonpath-reference",
                    path=str(path),
                    message=(
                        "File mentions .openclaw but not as a filesystem path. Classify manually: "
                        "service labels and historical examples are cleanup-immune; live paths are not."
                    ),
                )
            )
    return findings


def audit(openclaw_root: Path, hermes_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(audit_cron_scanner(openclaw_root, hermes_root))
    findings.extend(audit_unmigrated_workflows(openclaw_root, hermes_root))
    findings.extend(audit_live_openclaw_paths(hermes_root))
    return findings


def render_markdown(findings: Sequence[Finding]) -> str:
    if not findings:
        return "# OpenClaw → Hermes migration audit\n\nNo findings. Cleanup-safety gate passed.\n"
    lines = ["# OpenClaw → Hermes migration audit", ""]
    for finding in findings:
        lines.extend(
            [
                f"## {finding.severity.upper()}: {finding.kind}",
                "",
                f"- path: `{finding.path}`",
                f"- message: {finding.message}",
            ]
        )
        if finding.details:
            lines.append("- details:")
            for key, value in finding.details.items():
                lines.append(f"  - {key}: `{value}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-root", type=Path, default=Path.home() / ".openclaw")
    parser.add_argument("--hermes-root", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings as well as blockers (default: blockers only)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    findings = audit(args.openclaw_root.expanduser(), args.hermes_root.expanduser())
    if args.json:
        print(json.dumps([asdict(finding) for finding in findings], indent=2, sort_keys=True))
    else:
        print(render_markdown(findings), end="")
    severities = {finding.severity for finding in findings}
    if BLOCKER in severities or (args.strict and WARNING in severities):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
