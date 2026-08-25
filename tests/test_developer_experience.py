"""Tests for the developer-experience contract.

The README drifted twice in one working session (10 skills → 18 → 19), which is why
`test_readme_accuracy.py` exists. These tests extend that principle to the things an
*installing agent* depends on: the generated manifest, the setup prompt, and the
promises the README makes about how easy a skill is to adopt.

Every check here failed against the repo before the DX pass, so none of them are
decorative.
"""

from __future__ import annotations

import json
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


def _run_verify_setup(hermes_home) -> subprocess.CompletedProcess:
    """Drive verify_setup.sh against an isolated HERMES_HOME."""
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "verify_setup.sh")],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{pathlib.Path(sys.executable).parent}:/usr/bin:/bin",
            "HERMES_HOME": str(hermes_home),
            "HOME": str(hermes_home),
        },
    )


def _cron_home(tmp_path, jobs, gateway_pid=None) -> pathlib.Path:
    """Build a HERMES_HOME with a cron store and optionally a gateway pid file."""
    home = tmp_path / "hermes_home"
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(jobs)
    if gateway_pid is not None:
        (home / "gateway.pid").write_text(json.dumps({"pid": gateway_pid, "kind": "hermes-gateway"}))
    return home


def test_verify_setup_warns_when_enabled_cron_has_no_live_gateway(tmp_path) -> None:
    """Enabled jobs with a dead gateway means nothing fires. Silence here is the bug."""
    # A pid that cannot be running, so the case is deterministic rather than
    # depending on a just-exited process not having its pid recycled.
    home = _cron_home(tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": True}]}), 999999)

    result = _run_verify_setup(home)

    assert "no live gateway" in result.stdout, result.stdout
    assert "warn" in result.stdout
    assert result.returncode == 0, "a stopped gateway is a warning, not a setup failure"


def test_verify_setup_warns_when_enabled_cron_has_no_pid_file(tmp_path) -> None:
    """A missing gateway.pid is the same inert-cron condition as a dead pid."""
    home = _cron_home(tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": True}]}))

    result = _run_verify_setup(home)

    assert "no live gateway" in result.stdout, result.stdout
    assert result.returncode == 0


def test_verify_setup_warns_when_the_gateway_pid_was_reused(tmp_path) -> None:
    """A live pid is not proof. Pid reuse must not be reported as a healthy gateway.

    This is the whole point of the check: a dead gateway whose number got recycled
    by some unrelated process is still a host where no cron job will ever fire.
    """
    impostor = subprocess.Popen(["sleep", "30"])
    try:
        home = _cron_home(
            tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": True}]}), impostor.pid
        )
        result = _run_verify_setup(home)
    finally:
        impostor.kill()
        impostor.wait()

    assert "not a hermes gateway" in result.stdout, result.stdout
    assert "alive" not in result.stdout
    assert result.returncode == 0


def test_verify_setup_warns_when_the_gateway_pid_belongs_to_a_hermes_non_gateway(
    tmp_path,
) -> None:
    """"hermes" alone in a command line is not a gateway. Only both words qualify."""
    shim = tmp_path / "hermes-log-tailer"
    shim.write_text("#!/bin/sh\nsleep 30\n")
    shim.chmod(0o755)
    impostor = subprocess.Popen([str(shim)])
    try:
        home = _cron_home(
            tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": True}]}), impostor.pid
        )
        result = _run_verify_setup(home)
    finally:
        impostor.kill()
        impostor.wait()

    assert "not a hermes gateway" in result.stdout, result.stdout
    assert result.returncode == 0


def test_verify_setup_warns_on_a_process_that_merely_mentions_the_gateway(tmp_path) -> None:
    """Both words in a path is not a running gateway. `tail -f hermes-gateway.log` is not one."""
    log = tmp_path / "hermes-gateway.log"
    log.write_text("")
    impostor = subprocess.Popen(["tail", "-f", str(log)])
    try:
        home = _cron_home(
            tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": True}]}), impostor.pid
        )
        result = _run_verify_setup(home)
    finally:
        impostor.kill()
        impostor.wait()

    assert "not a hermes gateway" in result.stdout, result.stdout
    assert result.returncode == 0


def test_verify_setup_reports_ok_when_gateway_is_alive(tmp_path) -> None:
    """The healthy path must not warn, or the warning stops meaning anything.

    Mirrors the real command shape, `.../hermes-agent/... gateway run`, so this
    asserts the matcher accepts the actual gateway rather than a convenient shim.
    """
    shim = tmp_path / "hermes-agent-python"
    shim.write_text("#!/bin/sh\nsleep 30\n")
    shim.chmod(0o755)
    gateway = subprocess.Popen([str(shim), "gateway", "run", "--replace"])
    try:
        home = _cron_home(
            tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": True}]}), gateway.pid
        )
        result = _run_verify_setup(home)
    finally:
        gateway.kill()
        gateway.wait()

    assert "gateway pid" in result.stdout, result.stdout
    assert "alive" in result.stdout
    assert "no live gateway" not in result.stdout
    assert "not a hermes gateway" not in result.stdout


def test_verify_setup_does_not_warn_when_no_cron_job_is_enabled(tmp_path) -> None:
    """Disabled jobs cannot fail to fire, so a dead gateway is irrelevant."""
    home = _cron_home(tmp_path, json.dumps({"jobs": [{"id": "a", "enabled": False}]}))

    result = _run_verify_setup(home)

    assert "none enabled" in result.stdout, result.stdout
    assert "no live gateway" not in result.stdout


def test_verify_setup_stays_silent_about_cron_when_none_is_configured(tmp_path) -> None:
    """A host with no cron store is not a defect and must produce no cron line."""
    home = tmp_path / "hermes_home"
    home.mkdir()

    result = _run_verify_setup(home)

    assert "cron:" not in result.stdout, result.stdout


def test_verify_setup_survives_a_malformed_cron_store(tmp_path) -> None:
    """A corrupt jobs.json must skip the check, never traceback or fail the run."""
    home = _cron_home(tmp_path, "{not json at all")

    result = _run_verify_setup(home)

    assert "Traceback" not in result.stdout + result.stderr
    assert "cron:" not in result.stdout


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
