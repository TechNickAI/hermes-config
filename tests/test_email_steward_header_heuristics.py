from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "email-steward"
    / "scripts"
    / "header_heuristics.py"
)
SPEC = importlib.util.spec_from_file_location("email_steward_header_heuristics", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_vip_sender_beats_bulk_headers():
    headers = {
        "from": "Important Person <vip@example.com>",
        "list-unsubscribe": "<mailto:leave@example.com>",
        "precedence": "bulk",
    }
    result = MODULE.classify(headers, {"vip@example.com"}, set())
    assert result["verdict"] == "important"
    assert result["signal"] == "vip_sender"


def test_vip_domain_beats_campaign_header():
    headers = {"from": "Person <person@example.com>", "x-mailchimp": "campaign"}
    result = MODULE.classify(headers, set(), {"example.com"})
    assert result["verdict"] == "important"
    assert result["signal"] == "vip_domain"


def test_list_unsubscribe_is_promotional():
    headers = {
        "from": "News <news@updates.example>",
        "list-unsubscribe": "<https://updates.example/unsubscribe>",
    }
    result = MODULE.classify(headers)
    assert result["verdict"] == "promotional"
    assert result["signal"] == "list_unsubscribe"


def test_reply_to_list_is_not_classified_as_broadcast():
    headers = {
        "from": "Person <person@example.com>",
        "list-id": "Project List <project.lists.example>",
        "in-reply-to": "<prior@example.com>",
    }
    result = MODULE.classify(headers)
    assert result["verdict"] == "ambiguous"


def test_noreply_localpart_is_automated_not_disposable():
    result = MODULE.classify({"from": "Service <noreply@service.example>"})
    assert result["verdict"] == "automated"
    assert result["signal"] == "sender_localpart"


def test_transactional_delivery_platform_is_automated_not_promotional():
    result = MODULE.classify(
        {
            "from": "Account Team <onboarding@service.example>",
            "x-ses-outgoing": "delivery-id",
            "subject": "Account action required",
        }
    )
    assert result["verdict"] == "automated"
    assert result["signal"] == "x-ses-outgoing"


def test_security_sender_without_bulk_headers_falls_through():
    result = MODULE.classify(
        {"from": "Security Team <security@service.example>", "subject": "Action required"}
    )
    assert result["verdict"] == "ambiguous"


def test_rfc822_parser_ignores_cli_warning_preamble():
    raw = (
        "2026-01-01 WARN imap codec repaired response\n"
        "From: Person <person@example.com>\n"
        "List-Unsubscribe: <mailto:leave@example.com>\n\nbody"
    )
    headers = MODULE.parse_rfc822_headers(raw)
    assert headers["from"] == "Person <person@example.com>"
    assert "list-unsubscribe" in headers


def test_empty_rfc822_input_fails_closed():
    try:
        MODULE.parse_rfc822_headers("warning with no message headers")
    except ValueError as exc:
        assert "no headers" in str(exc)
    else:
        raise AssertionError("missing headers should fail")


def test_rfc822_parser_stops_before_body():
    raw = "From: Person <person@example.com>\nSubject: Hello\n\nList-Unsubscribe: injected"
    headers = MODULE.parse_rfc822_headers(raw)
    assert "list-unsubscribe" not in headers
    assert headers["subject"] == "Hello"


def test_gog_parser_uses_latest_message_headers():
    raw = json.dumps(
        {
            "thread": {
                "messages": [
                    {"payload": {"headers": [{"name": "From", "value": "old@example.com"}]}},
                    {
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "new@example.com"},
                                {"name": "List-Unsubscribe", "value": "<mailto:leave@example.com>"},
                            ]
                        }
                    },
                ]
            }
        }
    )
    headers = MODULE.parse_gog_thread_json(raw)
    assert headers["from"] == "new@example.com"
    assert "list-unsubscribe" in headers


def test_malformed_gog_json_fails_closed():
    try:
        MODULE.parse_gog_thread_json("{}")
    except ValueError as exc:
        assert "no messages" in str(exc)
    else:
        raise AssertionError("malformed thread JSON should fail")
