"""Tests that keep the README honest about what this repo actually contains.

The README is the first thing anyone sees, and it drifted badly before: five of
six directories were marked "(planned)" while all five shipped, and the skills
table went stale within hours when new skills landed. Documentation claims about
*inventory* are mechanically checkable, so check them mechanically instead of
relying on whoever edits next to remember.

These tests deliberately verify only what can be verified without judgement —
that every documented artifact exists and every existing artifact is documented.
Whether the prose is any good is a human's call.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _documented_skills() -> set[str]:
    """Skill names from the README's skills table (rows like `| `name` | ... |`)."""
    return set(re.findall(r"^\| `([a-z0-9-]+)`\s*\|", _readme(), re.MULTILINE))


def _actual_skills() -> set[str]:
    return {p.name for p in (REPO_ROOT / "skills").iterdir() if p.is_dir()}


def test_every_shipped_skill_is_documented() -> None:
    """A skill nobody can discover may as well not exist."""
    missing = _actual_skills() - _documented_skills()
    assert not missing, (
        f"skills present on disk but absent from the README table: {sorted(missing)}. "
        "Add a row to the skills table in README.md."
    )


def test_every_documented_skill_exists() -> None:
    """The README must not promise skills that aren't here."""
    phantom = _documented_skills() - _actual_skills()
    assert not phantom, (
        f"skills listed in the README but not on disk: {sorted(phantom)}. "
        "Remove the row or restore the skill."
    )


def test_skill_count_in_heading_matches_reality() -> None:
    """The '### skills/ — N procedural skills' heading states a real count."""
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty-two": 22,
        "twenty-three": 23, "twenty-four": 24, "twenty-five": 25,
    }
    match = re.search(r"### `skills/` — ([\w-]+) procedural skills", _readme())
    assert match, "could not find the skills heading in README.md"
    claimed_word = match.group(1).lower()
    # The heading may spell the count ("nineteen") or use a numeral ("19"); both are
    # legitimate prose choices and neither should fail this test for style reasons.
    claimed = words.get(claimed_word)
    if claimed is None and claimed_word.isdigit():
        claimed = int(claimed_word)
    assert claimed is not None, f"unrecognised count word in skills heading: {claimed_word!r}"
    actual = len(_actual_skills())
    assert claimed == actual, (
        f"README heading claims {claimed} skills ({claimed_word}) but {actual} exist on disk"
    )


def test_readme_relative_links_resolve() -> None:
    """Every relative markdown link in the README points at a real file."""
    broken = []
    for _text, link in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", _readme()):
        if link.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = link.split("#")[0]
        if target and not (REPO_ROOT / target).exists():
            broken.append(link)
    assert not broken, f"README links to nonexistent paths: {sorted(set(broken))}"


def test_readme_does_not_market_shipped_work_as_planned() -> None:
    """The '(planned)' marker caused the original drift — keep it out.

    Every directory it was attached to had in fact shipped, so a visitor was told
    the repo was nearly empty. If something genuinely is planned, it belongs in a
    GitHub issue, not in the README's inventory.
    """
    assert "_(planned)_" not in _readme(), (
        "README marks something as '(planned)'. Track unbuilt work in issues; the "
        "README should describe what a visitor can actually use today."
    )


def test_documented_top_level_directories_exist() -> None:
    """Directories named in the 'What's in here' section are real."""
    for directory in re.findall(r"^### `([a-z]+)/", _readme(), re.MULTILINE):
        assert (REPO_ROOT / directory).is_dir(), (
            f"README documents a `{directory}/` section but that directory does not exist"
        )
