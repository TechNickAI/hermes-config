"""Tests for the user-profile-audit scorer.

All fixtures are SYNTHETIC. Never put real memory content in this repo.

Two-way discipline: every detector needs a MUST-FLAG case and a MUST-NOT-FLAG
control. A detector that fires on everything is as useless as one that never fires.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "user-profile-audit"
    / "scripts"
    / "audit_user_md.py"
)

spec = importlib.util.spec_from_file_location("audit_user_md", SCRIPT)
audit_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_mod)

DELIM = "\n\u00a7\n"


def codes(text, cap=1375):
    return {f["code"] for f in audit_mod.group(audit_mod.scan(text, cap))}


# --- A clean file must stay clean (the control that makes the rest meaningful) ---

CLEAN = DELIM.join(
    [
        "Example User: staff engineer, expert in Python and distributed systems, "
        "beginner at frontend accessibility. Treat as an expert peer on backend work.",
        "She wants the answer first, then the evidence. No preamble.",
        "She judges work by whether a claim was actually exercised, not by a passing build.",
        "She wants pushback stated once, then the decision is hers.",
    ]
)


def test_clean_profile_has_no_findings():
    assert codes(CLEAN) == set()


# --- SECRET ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sample",
    [
        "Accounts 1111/2222/3333 = the first org; 4444 = the second org.",
        "api_key: sk-abcd1234efgh5678ijkl",
        "Her SSN is 123-45-6789.",
        "Card on file 4111 1111 1111 1111 for booking.",
    ],
)
def test_secret_patterns_flag(sample):
    assert "SECRET" in codes(sample)


@pytest.mark.parametrize(
    "sample",
    [
        "She has three accounts and wants them kept separate.",
        "Use the business account for anything vendor related.",
        "He works in a team of 4444 people across 12 offices.",
    ],
)
def test_secret_controls_do_not_flag(sample):
    # Labels and bare counts must not trip the account detector.
    assert "SECRET" not in codes(sample)


def test_secret_evidence_is_redacted_in_rendered_output(capsys):
    result = {
        "profile_home": "/synthetic",
        "user_chars": 40,
        "user_entries": 1,
        "user_cap": 1375,
        "user_cap_raised": False,
        "findings": audit_mod.group(audit_mod.scan("api_key: sk-abcd1234efgh5678", 1375)),
    }
    audit_mod.render(result)
    out = capsys.readouterr().out
    assert "sk-abcd1234efgh5678" not in out
    assert "<redacted>" in out


def test_secret_evidence_is_redacted_in_the_data_structure_itself():
    """Redaction must happen before JSON serialization, not only at render time.

    A render-time-only redaction leaks through --json and through any future
    output mode someone adds.
    """
    findings = audit_mod.group(audit_mod.scan("api_key: sk-abcd1234efgh5678", 1375))
    blob = json.dumps(findings)
    assert "sk-abcd1234efgh5678" not in blob
    assert "<redacted>" in blob


def test_non_secret_evidence_is_not_redacted():
    """Redacting everything would make the report useless. Only SECRET is masked."""
    findings = audit_mod.group(audit_mod.scan("He is currently focused on that.", 1375))
    blob = json.dumps(findings)
    assert "<redacted>" not in blob
    assert "currently" in blob


# --- PII ------------------------------------------------------------------


def test_pii_flags_address_phone_email():
    found = codes("Lives at 1234 Example Ave, Springfield. Phone +1 555-555-0143. "
        "Email user@example.com")
    assert "PII" in found


def test_pii_control_city_only():
    assert "PII" not in codes("She is based in Springfield and works Central time.")


# --- TRANSIENT ------------------------------------------------------------


@pytest.mark.parametrize(
    "sample",
    [
        "Switched from WhatsApp 2026-02-12.",
        "He is currently focused on the reporting stack.",
        "Waiting on PR #123 to land.",
        "That migration is blocked on the vendor.",
    ],
)
def test_transient_flags(sample):
    assert "TRANSIENT" in codes(sample)


def test_transient_control_stable_preference():
    assert "TRANSIENT" not in codes("He prefers short answers with the conclusion first.")


# --- MISFILED -------------------------------------------------------------


def test_misfiled_flags_paths_and_mechanics():
    found = codes("State lives at ~/.config/thing and deploy runs from the main branch.")
    assert "MISFILED" in found


def test_misfiled_control_plain_preference():
    assert "MISFILED" not in codes("She likes plain English with no jargon.")


def test_misfiled_prose_does_not_manufacture_an_overflow_signature(tmp_path):
    """A mechanics WORD in ordinary prose must not fake the overflow signature.

    'She reviews every schema change' is a legitimate preference line. Only a hard
    signal (path, command, host) may corroborate overflow.
    """
    home = tmp_path / "prose"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text(
        "She reviews every schema change and wants a deploy-free explanation.",
        encoding="utf-8",
    )
    (home / "memories" / "MEMORY.md").write_text("m" * 2190, encoding="utf-8")

    result = audit_mod.audit(home)
    assert "OVERFLOW" not in {f["code"] for f in result["findings"]}


# --- Structure ------------------------------------------------------------


def test_monolith_flagged():
    assert "MONOLITH" in codes("x" * 500)


def test_short_single_entry_is_not_a_monolith():
    assert "MONOLITH" not in codes("She prefers concise answers.")


def test_fat_entry_flagged():
    assert "FAT_ENTRY" in codes("y" * 700)


def test_over_cap_and_near_cap():
    assert "OVER_CAP" in codes("z" * 1500, cap=1375)
    assert "NEAR_CAP" in codes("z" * 1200, cap=1375)
    assert "NEAR_CAP" not in codes("z" * 500, cap=1375)


def test_over_cap_and_near_cap_are_mutually_exclusive():
    found = codes("z" * 1500, cap=1375)
    assert "OVER_CAP" in found and "NEAR_CAP" not in found


# --- Phrasing -------------------------------------------------------------


def test_imperative_flagged():
    assert "IMPERATIVE" in codes("Always respond concisely.")


def test_declarative_equivalent_not_flagged():
    assert "IMPERATIVE" not in codes("She prefers concise responses.")


# --- Caps / discovery -----------------------------------------------------


def test_raised_cap_is_read_from_profile_config(tmp_path):
    home = tmp_path / "profile"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text("a" * 2000, encoding="utf-8")
    (home / "config.yaml").write_text("memory:\n  user_char_limit: 5000\n", encoding="utf-8")

    result = audit_mod.audit(home)
    assert result is not None
    if result["user_cap"] == 5000:  # yaml available
        assert result["user_cap_raised"] is True
        assert "OVER_CAP" not in {f["code"] for f in result["findings"]}
    else:  # pyyaml missing -> falls back to the documented default
        assert result["user_cap"] == audit_mod.DEFAULT_USER_CAP


def test_missing_user_md_returns_none(tmp_path):
    home = tmp_path / "empty"
    (home / "memories").mkdir(parents=True)
    assert audit_mod.audit(home) is None


def test_discover_finds_root_and_named_profiles(tmp_path):
    (tmp_path / "memories").mkdir()
    (tmp_path / "profiles" / "alpha" / "memories").mkdir(parents=True)
    (tmp_path / "profiles" / "beta" / "memories").mkdir(parents=True)
    (tmp_path / "profiles" / "not-a-profile").mkdir(parents=True)

    found = {p.name for p in audit_mod.discover(tmp_path)}
    assert "alpha" in found and "beta" in found
    assert "not-a-profile" not in found


def test_overflow_signature_detected(tmp_path):
    home = tmp_path / "p"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text(
        "She likes short answers.\n\u00a7\nState lives at ~/.config/thing and deploy runs from main.",
        encoding="utf-8",
    )
    (home / "memories" / "MEMORY.md").write_text("m" * 2190, encoding="utf-8")

    result = audit_mod.audit(home)
    assert "OVERFLOW" in {f["code"] for f in result["findings"]}


def test_no_overflow_signature_when_memory_is_small(tmp_path):
    home = tmp_path / "p2"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text(
        "State lives at ~/.config/thing and deploy runs from main.", encoding="utf-8"
    )
    (home / "memories" / "MEMORY.md").write_text("m" * 100, encoding="utf-8")

    result = audit_mod.audit(home)
    assert "OVERFLOW" not in {f["code"] for f in result["findings"]}


# --- Regressions proven by mutation testing during review --------------------


def test_long_digit_runs_above_card_length_still_flag():
    """A 17-19 digit account number is longer than a card and just as sensitive.

    An upper bound of {13,16} silently passed these through.
    """
    for n in (13, 16, 17, 19):
        assert "SECRET" in codes("Reference " + "9" * n), f"{n} digits not flagged"


def test_short_digit_runs_do_not_flag():
    assert "SECRET" not in codes("She has 12 offices and 2024 employees.")


def test_crlf_file_is_not_reported_as_a_monolith(tmp_path):
    """CRLF never matches the \n-delimited entry separator."""
    home = tmp_path / "crlf"
    (home / "memories").mkdir(parents=True)
    body = "First entry about her preferences.\r\n\u00a7\r\nSecond entry about her workflow."
    (home / "memories" / "USER.md").write_text(body, encoding="utf-8")

    result = audit_mod.audit(home)
    assert result["user_entries"] == 2
    assert "MONOLITH" not in {f["code"] for f in result["findings"]}


def test_non_numeric_cap_does_not_crash_the_run(tmp_path):
    """A hand-edited cap must not take down a whole fleet scan."""
    yaml = pytest.importorskip("yaml")  # noqa: F841
    for bad in ("notanumber", "null", '""'):
        home = tmp_path / f"bad-{bad.strip(chr(34)) or 'empty'}"
        (home / "memories").mkdir(parents=True)
        (home / "memories" / "USER.md").write_text("short", encoding="utf-8")
        (home / "config.yaml").write_text(f"memory:\n  user_char_limit: {bad}\n", encoding="utf-8")

        result = audit_mod.audit(home)
        assert result["user_cap"] == audit_mod.DEFAULT_USER_CAP


def test_cap_override_flags_are_reported(tmp_path):
    """Mutation-proven gap: both override flags were previously unasserted."""
    pytest.importorskip("yaml")
    home = tmp_path / "raised"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text("short", encoding="utf-8")
    (home / "config.yaml").write_text(
        "memory:\n  user_char_limit: 5000\n  memory_char_limit: 9000\n", encoding="utf-8"
    )

    result = audit_mod.audit(home)
    assert result["user_cap"] == 5000
    assert result["memory_cap"] == 9000
    assert result["user_cap_raised"] is True
    assert result["memory_cap_raised"] is True


def test_absent_overrides_are_reported_as_not_raised(tmp_path):
    pytest.importorskip("yaml")
    home = tmp_path / "plain"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text("short", encoding="utf-8")
    (home / "config.yaml").write_text("memory: {}\n", encoding="utf-8")

    result = audit_mod.audit(home)
    assert result["user_cap_raised"] is False
    assert result["memory_cap_raised"] is False
    assert result["user_cap"] == audit_mod.DEFAULT_USER_CAP


@pytest.mark.parametrize("value,expected_raised", [(1000, False), (1375, False), (5000, True)])
def test_raised_means_above_default_not_merely_present(tmp_path, value, expected_raised):
    """A cap set AT or BELOW the default is not a raised-cap finding.

    Keying this on "the key exists" labels a profile that pins the default, or
    tightens below it, as RAISED - a false finding for the auditor.
    """
    pytest.importorskip("yaml")
    home = tmp_path / f"cap-{value}"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text("short", encoding="utf-8")
    (home / "config.yaml").write_text(f"memory:\n  user_char_limit: {value}\n", encoding="utf-8")

    assert audit_mod.audit(home)["user_cap_raised"] is expected_raised


def test_nonnumeric_cap_falls_back_and_is_not_labelled_raised(tmp_path):
    pytest.importorskip("yaml")
    home = tmp_path / "junkcap"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text("short", encoding="utf-8")
    (home / "config.yaml").write_text("memory:\n  user_char_limit: notanumber\n", encoding="utf-8")

    result = audit_mod.audit(home)
    assert result["user_cap"] == audit_mod.DEFAULT_USER_CAP
    assert result["user_cap_raised"] is False


def test_unverified_cap_is_shown_in_the_text_report(capsys):
    """An operator must never see a cap number without knowing it was a guess."""
    audit_mod.render(
        {
            "profile_home": "/synthetic",
            "user_chars": 10,
            "user_entries": 1,
            "user_cap": audit_mod.DEFAULT_USER_CAP,
            "user_cap_raised": False,
            "config_note": "config.yaml present but unreadable (ScannerError) - caps are defaults",
            "findings": [],
        }
    )
    out = capsys.readouterr().out
    assert "CAP UNVERIFIED" in out
    assert "unreadable" in out


def test_a_missing_path_does_not_discard_the_other_profiles(tmp_path, capsys, monkeypatch):
    """One stale path in a fleet list must not wipe out every readable profile."""
    good = tmp_path / "good"
    (good / "memories").mkdir(parents=True)
    (good / "memories" / "USER.md").write_text("She prefers concise answers.", encoding="utf-8")

    monkeypatch.setattr(
        sys, "argv", ["audit_user_md.py", str(tmp_path / "nope"), str(good), "--json"]
    )
    rc = audit_mod.main()
    payload = json.loads(capsys.readouterr().out)

    assert rc == 3, "an incomplete run must not report success"
    assert len(payload["results"]) == 1, "the readable profile must still be audited"
    assert any("nope" in e["profile_home"] for e in payload["errors"])


def test_unparseable_config_is_surfaced_not_silently_defaulted(tmp_path):
    """Silently scoring against defaults produces false OVER_CAP on a raised profile."""
    pytest.importorskip("yaml")
    home = tmp_path / "broken"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text("short", encoding="utf-8")
    (home / "config.yaml").write_text("memory:\n  user_char_limit: [unclosed\n", encoding="utf-8")

    result = audit_mod.audit(home)
    assert result["config_note"], "an unreadable config must be reported to the operator"


def test_boundary_controls_are_pinned():
    """Off-by-one in either cap comparison would ship silently without these."""
    assert "OVER_CAP" not in codes("z" * 1375, cap=1375)
    assert "OVER_CAP" in codes("z" * 1376, cap=1375)
    assert "NEAR_CAP" not in codes("z" * 1168, cap=1375)  # just under 85%
    assert "FAT_ENTRY" not in codes("y" * 599)
    assert "FAT_ENTRY" in codes("y" * 601)


def test_overflow_never_outranks_a_high_finding(tmp_path):
    """Readers triage on the first line; a MED must not display above a HIGH."""
    home = tmp_path / "both"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "USER.md").write_text(
        "api_key: sk-abcd1234efgh5678\n\u00a7\nState lives at ~/.config/thing.",
        encoding="utf-8",
    )
    (home / "memories" / "MEMORY.md").write_text("m" * 2190, encoding="utf-8")

    codes_in_order = [f["code"] for f in audit_mod.audit(home)["findings"]]
    assert "OVERFLOW" in codes_in_order and "SECRET" in codes_in_order
    assert codes_in_order.index("SECRET") < codes_in_order.index("OVERFLOW")


# --- Redaction bypass + detector gaps found by adversarial review -------------


@pytest.mark.parametrize(
    "sample,code",
    [
        # A credential inside an over-long entry was captured verbatim by FAT_ENTRY.
        ("api_key: sk-SUPERSECRET123456789 " + "x" * 650, "FAT_ENTRY"),
        # A password that is also an email was captured verbatim by PII.
        ("password: my.secret.pw@example.com", "PII"),
        # The hard case: the PII slice contains NOTHING a secret pattern matches on
        # its own, because the `password:` label that made it secret is outside the
        # slice. Only span overlap against the source text catches this.
        ("password: ordinary@example.com", "PII"),
    ],
)
def test_secret_is_redacted_even_when_a_non_secret_detector_captures_it(sample, code):
    """Redaction must key on the CONTENT, not on which detector fired.

    Keying redaction on the finding's own code leaked credentials through every
    other detector's evidence field.
    """
    findings = audit_mod.group(audit_mod.scan(sample, 1375))
    target = [f for f in findings if f["code"] == code]
    assert target, f"expected a {code} finding for this fixture"
    assert target[0]["examples"] == ["<redacted>"]
    blob = json.dumps(findings)
    for secret_value in ("SUPERSECRET", "my.secret.pw", "ordinary@example.com"):
        assert secret_value not in blob


def test_legitimate_pii_is_still_shown_as_evidence():
    """Redacting all PII would make the report useless.

    An email with no credential context is a normal PII finding and the auditor
    needs to see it to apply the necessity test.
    """
    findings = audit_mod.group(audit_mod.scan("Her sender address is user@example.com", 1375))
    assert "user@example.com" in json.dumps(findings)


@pytest.mark.parametrize(
    "sample",
    [
        "Account number: 123456789012",   # 12-digit, between the old regex gaps
        "checking account 123456789",     # 9-digit US account
        "My password is Hunter2xy",       # natural language, no colon
        "routing num 123456789",          # short label word
    ],
)
def test_real_world_secret_shapes_are_flagged(sample):
    assert "SECRET" in codes(sample)


def test_bank_label_without_digits_is_not_a_secret():
    """The label itself must never become the HIGH-severity evidence."""
    assert "SECRET" not in codes("What is my routing number?")
