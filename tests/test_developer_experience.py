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


def run_verify_setup(hermes_home: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the smoke test against a synthetic HERMES_HOME.

    PATH is pinned to system directories so the result does not depend on whether
    the developer running the suite happens to have a hermes CLI installed. The
    assertions below therefore look at named findings rather than the exit status,
    which is set by unrelated checks such as the CLI probe.
    """
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "verify_setup.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HERMES_HOME": str(hermes_home), "HOME": str(hermes_home)},
    )


def write_skill(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n")


def test_verify_setup_accepts_category_skill_directories(tmp_path: pathlib.Path) -> None:
    """A category holding nested skills is a supported layout, not a broken copy.

    Hermes resolves skills by walking the tree (agent/skill_utils.py
    iter_skill_index_files), so a depth-1 directory without its own SKILL.md is
    only broken if nothing is behind it. Flagging categories buried real failures
    under dozens of false ones.
    """
    skills = tmp_path / "skills"
    write_skill(skills / "flat-skill")
    write_skill(skills / "github" / "nested-skill")
    (skills / "github" / "DESCRIPTION.md").write_text("GitHub skills.\n")

    result = run_verify_setup(tmp_path)

    assert "github has no SKILL.md" not in result.stdout
    assert "flat-skill has no SKILL.md" not in result.stdout
    assert "skills directory present (2 installed)" in result.stdout


def test_verify_setup_counts_skills_nested_more_than_one_level_deep(tmp_path: pathlib.Path) -> None:
    """Categories can hold sub-categories, so the search must not assume depth 2.

    mlops/inference/llama-cpp/SKILL.md is a real layout on the fleet. A depth-2
    search undercounts it, and a category holding only deeper skills would be
    reported as an incomplete copy.
    """
    skills = tmp_path / "skills"
    write_skill(skills / "mlops" / "inference" / "llama-cpp")
    write_skill(skills / "mlops" / "research" / "dspy")
    (skills / "mlops" / "DESCRIPTION.md").write_text("MLOps skills.\n")

    result = run_verify_setup(tmp_path)

    assert "mlops has no SKILL.md" not in result.stdout
    assert "skills directory present (2 installed)" in result.stdout


def test_verify_setup_does_not_count_support_directory_markdown(tmp_path: pathlib.Path) -> None:
    """references/ and friends hold progressive-disclosure data, not skill roots.

    Hermes prunes them (SKILL_SUPPORT_DIRS), so an archived SKILL.md under
    references/ must not inflate the count or make an empty directory look whole.
    """
    skills = tmp_path / "skills"
    write_skill(skills / "category" / "real-skill")
    (skills / "category" / "DESCRIPTION.md").write_text("A category.\n")
    write_skill(skills / "category" / "real-skill" / "references" / "archived-copy")
    (skills / "broken" / "references").mkdir(parents=True)
    (skills / "broken" / "references" / "SKILL.md").write_text("archived, not a skill\n")

    result = run_verify_setup(tmp_path)

    assert "skills directory present (1 installed)" in result.stdout
    assert "broken has no SKILL.md" in result.stdout


def test_verify_setup_still_fails_an_empty_skill_directory(tmp_path: pathlib.Path) -> None:
    """The original contract holds: neither its own SKILL.md nor any nested one is broken."""
    skills = tmp_path / "skills"
    write_skill(skills / "good-skill")
    (skills / "truncated-copy").mkdir(parents=True)
    (skills / "truncated-copy" / "README.md").write_text("half a skill\n")

    result = run_verify_setup(tmp_path)

    assert "truncated-copy has no SKILL.md" in result.stdout
    assert "good-skill has no SKILL.md" not in result.stdout


def test_verify_setup_fails_an_empty_category(tmp_path: pathlib.Path) -> None:
    """DESCRIPTION.md with nothing behind it resolves to no skills, so it still fails.

    The marker file being intact does not help the user: the directory loads
    nothing, which is the same outcome as an incomplete copy. Only the message
    changes, because pointing at a missing SKILL.md would be wrong for a category.
    """
    skills = tmp_path / "skills"
    (skills / "placeholder").mkdir(parents=True)
    (skills / "placeholder" / "DESCRIPTION.md").write_text("Coming soon.\n")

    result = run_verify_setup(tmp_path)

    assert "FAIL" in result.stdout
    assert "placeholder is an empty category" in result.stdout
    assert "placeholder has no SKILL.md" not in result.stdout


def test_verify_setup_ignores_hermes_metadata_directories(tmp_path: pathlib.Path) -> None:
    """.hub and .curator_backups are Hermes internals, which Hermes itself prunes."""
    skills = tmp_path / "skills"
    write_skill(skills / "real-skill")
    (skills / ".hub").mkdir(parents=True)
    (skills / ".curator_backups" / "2026-01-01").mkdir(parents=True)

    result = run_verify_setup(tmp_path)

    assert ".hub" not in result.stdout
    assert ".curator_backups" not in result.stdout


def test_verify_setup_accepts_cortex_selected_only_by_a_profile(tmp_path: pathlib.Path) -> None:
    """A fleet may leave the root provider unset while every profile selects cortex."""
    (tmp_path / "plugins" / "cortex").mkdir(parents=True)
    (tmp_path / "config.yaml").write_text("memory:\n  provider: ''\n")
    profile = tmp_path / "profiles" / "argus"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("memory:\n  provider: cortex\n")

    result = run_verify_setup(tmp_path)

    assert "the plugin is inert" not in result.stdout
    assert "cortex selected by profile(s): argus" in result.stdout


def test_verify_setup_still_fails_when_nothing_selects_cortex(tmp_path: pathlib.Path) -> None:
    """The original contract holds: a copied plugin nobody points at is still inert."""
    (tmp_path / "plugins" / "cortex").mkdir(parents=True)
    (tmp_path / "config.yaml").write_text("memory:\n  provider: ''\n")
    profile = tmp_path / "profiles" / "other"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("memory:\n  provider: sqlite\n")

    result = run_verify_setup(tmp_path)

    assert "the plugin is inert" in result.stdout


def test_verify_setup_accepts_quoted_cortex_in_a_profile(tmp_path: pathlib.Path) -> None:
    """provider: "cortex" and 'cortex' are valid YAML and must not read as unset."""
    (tmp_path / "plugins" / "cortex").mkdir(parents=True)
    for name, value in (("single", "'cortex'"), ("double", '"cortex"')):
        profile = tmp_path / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text(f"memory:\n  provider: {value}\n")

    result = run_verify_setup(tmp_path)

    assert "the plugin is inert" not in result.stdout
    assert "cortex selected by profile(s): double, single" in result.stdout


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
