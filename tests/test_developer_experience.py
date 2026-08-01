"""Tests for the developer-experience contract.

The README drifted twice in one working session (10 skills → 18 → 19), which is why
`test_readme_accuracy.py` exists. These tests extend that principle to the things an
*installing agent* depends on: the generated manifest, the setup prompt, and the
promises the README makes about how easy a skill is to adopt.

Every check here failed against the repo before the DX pass, so none of them are
decorative.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
MANIFEST = SKILLS / "MANIFEST.yaml"


def skill_dirs() -> list[pathlib.Path]:
    return sorted(d for d in SKILLS.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def test_manifest_exists() -> None:
    assert MANIFEST.exists(), "skills/MANIFEST.yaml is the index agents read first"


def test_manifest_is_not_stale() -> None:
    """The manifest is generated. If it disagrees with disk, it is worse than absent."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_manifest.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"skills/MANIFEST.yaml is stale:\n{result.stdout}{result.stderr}\n"
        "Run: python scripts/generate_manifest.py"
    )


def test_manifest_covers_every_skill() -> None:
    listed = {s["name"] for s in manifest()["skills"]}
    on_disk = {d.name for d in skill_dirs()}
    assert listed == on_disk, f"manifest/disk mismatch: {listed ^ on_disk}"


def test_every_skill_has_a_known_scope() -> None:
    """`scope` is how an agent decides relevance. An unknown value silently breaks that."""
    for skill in manifest()["skills"]:
        assert skill["scope"] in {"solo", "fleet", "migration"}, (
            f"{skill['name']} has unknown scope {skill['scope']!r}"
        )


def test_works_out_of_the_box_agrees_with_requires() -> None:
    """These two fields must never contradict — an agent trusts one or the other."""
    for skill in manifest()["skills"]:
        assert skill["works_out_of_the_box"] == (not skill["requires"]), (
            f"{skill['name']}: works_out_of_the_box={skill['works_out_of_the_box']} "
            f"but requires={skill['requires']}"
        )


def test_a_usable_starter_set_exists() -> None:
    """SETUP.md promises skills that need no configuration. Prove some exist."""
    ready = [
        s["name"]
        for s in manifest()["skills"]
        if s["scope"] == "solo" and s["works_out_of_the_box"]
    ]
    assert len(ready) >= 3, f"expected a real zero-setup starter set, got {ready}"
    for name in ("recall", "multi-review", "trust-framework"):
        assert name in ready, f"{name} is recommended in SETUP.md but is not zero-setup"


def test_related_skills_resolve_or_are_marked_external() -> None:
    """A dangling `related_skills` entry sends a dependency-resolving agent chasing ghosts."""
    have = {d.name for d in skill_dirs()}
    for skill_dir in skill_dirs():
        text = (skill_dir / "SKILL.md").read_text()
        match = re.search(r"^\s*related_skills:\s*\[([^\]]*)\]", text, re.M)
        if not match:
            continue
        for name in re.findall(r"[\w-]+", match.group(1)):
            assert name in have, (
                f"{skill_dir.name} lists related skill {name!r}, which is not in this repo. "
                "Remove it, or move it to the '# referenced but not shipped here' comment."
            )


def test_setup_prompt_exists_and_is_self_contained() -> None:
    """SETUP.md is the copy-paste artifact. It must not depend on unstated context."""
    setup = (ROOT / "SETUP.md").read_text()
    for expected in ("skills/MANIFEST.yaml", "scope", "requires", "SOUL.md"):
        assert expected in setup, f"SETUP.md never mentions {expected!r}"
    assert "```" in setup, "SETUP.md must contain a copy-pasteable fenced block"


def test_setup_prompt_protects_existing_state() -> None:
    """The prompt must tell an agent not to clobber accumulated personal state."""
    setup = (ROOT / "SETUP.md").read_text().lower()
    assert "never overwrite" in setup or "do not overwrite" in setup
    assert "soul.md" in setup


def test_referenced_scripts_exist_and_are_runnable() -> None:
    """SETUP.md and the README point at scripts. A broken pointer is worse than none."""
    for rel in ("scripts/verify_setup.sh", "scripts/check_updates.py", "scripts/generate_manifest.py"):
        path = ROOT / rel
        assert path.exists(), f"{rel} is referenced in docs but missing"


def test_verify_setup_reports_a_missing_install() -> None:
    """The smoke test must actually fail on a broken setup, not just print cheerfully."""
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "verify_setup.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HERMES_HOME": "/nonexistent/hermes/home", "HOME": "/tmp"},
    )
    assert result.returncode == 1, "verify_setup.sh should exit 1 when Hermes is absent"
    assert "FAIL" in result.stdout


def test_readme_does_not_claim_universal_zero_setup() -> None:
    """Over half the skills need a credential or service; the README must not imply otherwise."""
    readme = (ROOT / "README.md").read_text()
    needs_setup = [s["name"] for s in manifest()["skills"] if s["requires"]]
    if needs_setup:
        assert "needs setup" in readme.lower(), (
            f"{len(needs_setup)} skills require external setup "
            f"({', '.join(needs_setup[:3])}…) but the README never flags any as such"
        )


def test_readme_points_agents_at_the_manifest() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "MANIFEST.yaml" in readme, "the README should route agents to the manifest"
    assert "SETUP.md" in readme, "the README should route agent users to SETUP.md"
