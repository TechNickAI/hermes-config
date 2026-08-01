#!/usr/bin/env python3
"""Report which installed skills have drifted from this repo's versions.

Copying is one-way. Nothing here phones home, nothing auto-updates, and that is
deliberate — but it means a skill copied months ago can silently fall behind without
anyone noticing. This is the missing half of that bargain: a read-only report.

It NEVER writes to ~/.hermes/. Divergence is often correct — Hermes rewrites its own
skills as it learns, so a locally-modified skill is usually a feature, not a problem.
This tells you where you stand and leaves the decision to you.

    python scripts/check_updates.py
    python scripts/check_updates.py --hermes-home /custom/path
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def parse_version(skill_md: pathlib.Path) -> str:
    """Read `version:` out of a SKILL.md frontmatter block."""
    try:
        text = skill_md.read_text()
    except OSError:
        return "?"
    match = re.search(r"^version:\s*['\"]?([^'\"\n]+)", text, re.M)
    return match.group(1).strip() if match else "?"


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def version_key(version: str) -> tuple:
    """Sortable key for a dotted version; unknowns sort lowest."""
    parts = []
    for chunk in version.split("."):
        number = re.match(r"\d+", chunk)
        parts.append(int(number.group()) if number else -1)
    return tuple(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hermes-home",
        default=os.environ.get("HERMES_HOME", str(pathlib.Path.home() / ".hermes")),
        help="Hermes home directory (default: ~/.hermes)",
    )
    args = parser.parse_args()

    installed_root = pathlib.Path(args.hermes_home).expanduser() / "skills"
    if not installed_root.is_dir():
        print(f"No skills directory at {installed_root} — nothing installed yet.")
        return 0

    repo_skills = {
        d.name: d / "SKILL.md" for d in (REPO / "skills").iterdir() if (d / "SKILL.md").exists()
    }

    newer, identical, modified, unknown = [], [], [], []

    for installed in sorted(p for p in installed_root.iterdir() if p.is_dir()):
        local_md = installed / "SKILL.md"
        if not local_md.exists():
            continue
        repo_md = repo_skills.get(installed.name)
        if repo_md is None:
            unknown.append(installed.name)
            continue

        local_version, repo_version = parse_version(local_md), parse_version(repo_md)
        if digest(local_md) == digest(repo_md):
            identical.append((installed.name, local_version))
        elif version_key(repo_version) > version_key(local_version):
            newer.append((installed.name, local_version, repo_version))
        else:
            modified.append((installed.name, local_version, repo_version))

    print(f"Comparing {installed_root} against {REPO / 'skills'}\n")

    if newer:
        print("UPDATE AVAILABLE — this repo has a newer version:")
        for name, local, repo in newer:
            print(f"  {name:24} yours {local:8} -> repo {repo}")
            print(f"    diff:  diff {installed_root / name}/SKILL.md {REPO}/skills/{name}/SKILL.md")
        print()

    if modified:
        print("LOCALLY MODIFIED — differs from the repo, but not behind it:")
        for name, local, repo in modified:
            same = "same version" if local == repo else f"yours {local}, repo {repo}"
            print(f"  {name:24} {same}")
        print("  (usually correct — Hermes rewrites its own skills as it learns)\n")

    if unknown:
        print("NOT FROM THIS REPO — left alone:")
        for name in unknown:
            print(f"  {name}")
        print()

    if identical:
        print(f"UP TO DATE ({len(identical)}): {', '.join(n for n, _ in identical)}\n")

    if not newer:
        print("Nothing is behind this repo.")
    else:
        print(f"{len(newer)} skill(s) have a newer version here. Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
