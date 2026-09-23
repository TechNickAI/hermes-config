"""Tests for the developer-experience contract.

The README drifted twice in one working session (10 skills → 18 → 19), which is why
`test_readme_accuracy.py` exists. These tests extend that principle to the things an
*installing agent* depends on: the generated manifest, the setup prompt, and the
promises the README makes about how easy a skill is to adopt.

Every check here failed against the repo before the DX pass, so none of them are
decorative.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import time

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


# --------------------------------------------------------------- gateway code skew
#
# A gateway process serves the code it booted with. `git pull` does not restart it, so
# a long-lived gateway can drift far behind the checkout it runs from while every other
# check in verify_setup.sh reports healthy. These tests pin the detection contract.


def _fake_checkout(root: pathlib.Path, head_moved_at: int, commit_at: int) -> pathlib.Path:
    """Build a throwaway hermes-agent checkout with controlled HEAD-move and commit times.

    `head_moved_at` drives the reflog (when HEAD last actually moved) and `commit_at`
    drives the commit date. They are separate on purpose: checking out an older branch
    moves HEAD forward in time while the commit date goes backwards.
    """
    checkout = root / "hermes-agent"
    checkout.mkdir(parents=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_AUTHOR_DATE": f"@{commit_at} +0000",
        "GIT_COMMITTER_DATE": f"@{commit_at} +0000",
    }

    def run(*args: str) -> None:
        subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True, env=env)

    run("init", "-q", ".")
    (checkout / "f.txt").write_text("x\n")
    run("add", "f.txt")
    run("commit", "-qm", "seed")
    # A second HEAD move, timestamped independently, becomes the reflog reference point.
    env["GIT_COMMITTER_DATE"] = f"@{head_moved_at} +0000"
    run("checkout", "-q", "-b", "moved")
    return checkout


def _run_verify(home: pathlib.Path) -> subprocess.CompletedProcess:
    """Drive the script against a fake HERMES_HOME with a system-only PATH.

    The bare PATH matters: CI runners have no hermes CLI, and the skew block must
    behave identically there.
    """
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "verify_setup.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HERMES_HOME": str(home), "HOME": str(home)},
        timeout=60,
    )


@pytest.fixture
def fake_gateway():
    """Start a process whose argv matches the gateway matcher, and always reap it."""
    started = []

    def start(argv: str):
        # argv goes through the environment, not string interpolation, so a quote in a
        # future test argument cannot break out of the shell command.
        proc = subprocess.Popen(
            ["bash", "-c", 'exec -a "$FAKE_ARGV" sleep 60'], env={**os.environ, "FAKE_ARGV": argv}
        )
        started.append(proc)
        # Wait for the exec to land, otherwise pgrep races the argv rewrite.
        for _ in range(50):
            time.sleep(0.05)
            found = subprocess.run(["pgrep", "-f", argv], capture_output=True, text=True)
            if str(proc.pid) in found.stdout.split():
                return proc
        raise AssertionError(f"fake gateway argv never became visible to pgrep: {argv!r}")

    yield start
    for proc in started:
        proc.kill()
        proc.wait()


def test_stale_gateway_is_reported(tmp_path: pathlib.Path, fake_gateway) -> None:
    """A gateway that booted before HEAD last moved must be named, with its pid."""
    home = tmp_path / "hermes"
    home.mkdir()
    # HEAD moved far in the future, so any process alive now booted before it.
    _fake_checkout(home, head_moved_at=4_102_444_800, commit_at=4_102_444_800)
    proc = fake_gateway("hermes-argus-test gateway run --replace")

    result = _run_verify(home)

    assert "booted before" in result.stdout, result.stdout
    assert str(proc.pid) in result.stdout, result.stdout
    assert "code on disk" in result.stdout, "the warning must name the remedy"


def test_stale_gateway_is_a_warning_not_a_failure(tmp_path: pathlib.Path, fake_gateway) -> None:
    """Deferring a restart is a legitimate state. Warn, never fail.

    Making this fatal would turn a routine condition into exit 1, which is the
    false-failure problem the verifier is supposed to be free of.
    """
    home = tmp_path / "hermes"
    home.mkdir()
    _fake_checkout(home, head_moved_at=4_102_444_800, commit_at=4_102_444_800)
    fake_gateway("hermes-argus-test gateway run --replace")

    result = _run_verify(home)

    assert "booted before" in result.stdout
    assert "FAIL  gateway" not in result.stdout, "code skew must not be a hard failure"


def test_current_gateway_is_not_reported(tmp_path: pathlib.Path, fake_gateway) -> None:
    """A gateway started after the last HEAD move is current. Silence is the contract."""
    home = tmp_path / "hermes"
    home.mkdir()
    # HEAD moved in the past, so a process started now is newer than the checkout.
    _fake_checkout(home, head_moved_at=1_000_000_000, commit_at=1_000_000_000)
    fake_gateway("hermes-argus-test gateway run --replace")

    result = _run_verify(home)

    assert "booted before" not in result.stdout, result.stdout


def test_skew_is_measured_from_head_movement_not_commit_date(
    tmp_path: pathlib.Path, fake_gateway
) -> None:
    """Checking out an older revision is skew, even though the commit date went backwards.

    This is the case a naive `git log -1 --format=%ct` comparison gets wrong: the
    working tree changed under the running gateway, but HEAD now points at an old
    commit, so the commit date looks older than the process and the drift is missed.
    """
    home = tmp_path / "hermes"
    home.mkdir()
    _fake_checkout(home, head_moved_at=4_102_444_800, commit_at=1_000_000_000)
    proc = fake_gateway("hermes-argus-test gateway run --replace")

    result = _run_verify(home)

    assert "booted before" in result.stdout, result.stdout
    assert str(proc.pid) in result.stdout


def test_no_checkout_means_no_skew_section(tmp_path: pathlib.Path, fake_gateway) -> None:
    """A uv/pip install has no git checkout. The block must skip, not error."""
    home = tmp_path / "hermes"
    home.mkdir()
    fake_gateway("hermes-argus-test gateway run --replace")

    result = _run_verify(home)

    assert "booted before" not in result.stdout
    assert "fatal" not in result.stdout.lower(), "git must never be run without a checkout"


def test_skew_check_does_not_disturb_the_missing_install_contract(
    tmp_path: pathlib.Path,
) -> None:
    """The pre-existing failure contract still holds with the new block in place."""
    result = _run_verify(tmp_path / "nonexistent")

    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_verifier_does_not_report_itself(tmp_path: pathlib.Path) -> None:
    """A shell whose own argv matches the pattern must not be reported as a gateway.

    `pgrep -f` matches the whole command line, so invoking the script from a wrapper
    whose arguments contain the pattern makes the verifier match its own process tree.
    Without an ancestry check it reports itself as stale code, which is nonsense.
    """
    home = tmp_path / "hermes"
    home.mkdir()
    _fake_checkout(home, head_moved_at=4_102_444_800, commit_at=4_102_444_800)
    pidfile = tmp_path / "wrapper.pid"

    # The wrapper's argv contains the matcher pattern, so this shell is in the pgrep set.
    script = (
        f'true "hermes gateway run"; echo $$ > "{pidfile}"; '
        f'bash "{ROOT / "scripts" / "verify_setup.sh"}"'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HERMES_HOME": str(home), "HOME": str(home)},
        timeout=60,
    )

    # Assert on our own pid rather than on the absence of all warnings: the developer
    # running this may have real gateways up, and those are legitimately reportable.
    wrapper_pid = pidfile.read_text().strip()
    assert f"pid {wrapper_pid} " not in result.stdout, result.stdout


def test_skew_check_survives_a_non_english_locale(tmp_path: pathlib.Path, fake_gateway) -> None:
    """`ps -o lstart=` is locale-dependent; the parse must not be."""
    locales = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=60).stdout
    locale = next(
        (name for name in ("fr_FR.UTF-8", "fr_FR.utf8", "de_DE.UTF-8") if name in locales.split()),
        None,
    )
    if locale is None:
        pytest.skip("no non-English locale installed, the assertion would pass vacuously")

    home = tmp_path / "hermes"
    home.mkdir()
    _fake_checkout(home, head_moved_at=4_102_444_800, commit_at=4_102_444_800)
    proc = fake_gateway("hermes-argus-test gateway run --replace")

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "verify_setup.sh")],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HERMES_HOME": str(home),
            "HOME": str(home),
            "LC_ALL": locale,
            "LANG": locale,
        },
        timeout=60,
    )

    assert "booted before" in result.stdout, result.stdout
    assert str(proc.pid) in result.stdout
