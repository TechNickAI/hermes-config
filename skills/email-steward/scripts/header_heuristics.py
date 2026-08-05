#!/usr/bin/env python3
"""Classify email headers without exposing message bodies to the orchestrator."""

from __future__ import annotations

import argparse
import email
import email.policy
import email.utils
import json
import re
import sys
from collections.abc import Mapping

PROMOTIONAL_LOCALPART = re.compile(
    r"(?:^|[._-])(?:no[-_.]?reply|newsletter|marketing|promo(?:tions)?|offers|deals)(?:$|[._-])",
    re.IGNORECASE,
)
CAMPAIGN_HEADERS = {
    "x-campaign",
    "x-hubspot",
    "x-mailchimp",
    "x-mailgun",
    "x-marketo",
    "x-mc-user",
    "x-postmark",
    "x-ses-outgoing",
    "x-sendgrid",
    "x-sg-eid",
}


def normalize_headers(items: object) -> dict[str, str]:
    """Normalize Gmail-style [{name, value}] headers to a lowercase mapping."""
    result: dict[str, str] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip().lower()
        if name:
            result[name] = str(item.get("value", "")).strip()
    return result


def parse_gog_thread_json(raw: str) -> dict[str, str]:
    data = json.loads(raw)
    thread = data.get("thread", data) if isinstance(data, Mapping) else {}
    messages = thread.get("messages", []) if isinstance(thread, Mapping) else []
    if not isinstance(messages, list) or not messages:
        raise ValueError("gog thread JSON contains no messages")
    latest = messages[-1]
    if not isinstance(latest, Mapping):
        raise ValueError("latest Gmail message is malformed")
    payload = latest.get("payload", {})
    if not isinstance(payload, Mapping):
        raise ValueError("latest Gmail payload is malformed")
    return normalize_headers(payload.get("headers", []))


def parse_rfc822_headers(raw: str) -> dict[str, str]:
    """Parse only the RFC 5322 header block before the first blank line."""
    # Some CLIs log warnings before the message. Ignore non-header preamble lines until
    # the first RFC-style header so harmless diagnostics cannot become fake headers.
    lines = raw.replace("\r\n", "\n").splitlines()
    while lines and not re.match(r"^[!-9;-~]+:\s*", lines[0]):
        lines.pop(0)
    header_lines: list[str] = []
    for line in lines:
        if not line:
            break
        header_lines.append(line)
    header_block = "\n".join(header_lines)
    message = email.message_from_string(header_block + "\n\n", policy=email.policy.default)
    return {str(name).lower(): str(value) for name, value in message.items()}


def sender_address(headers: Mapping[str, str]) -> str:
    return (email.utils.parseaddr(headers.get("from", ""))[1] or "").lower()


def classify(
    headers: Mapping[str, str],
    vip_senders: set[str] | None = None,
    vip_domains: set[str] | None = None,
) -> dict[str, str]:
    vip_senders = {item.lower() for item in (vip_senders or set())}
    vip_domains = {item.lower().lstrip("@") for item in (vip_domains or set())}
    address = sender_address(headers)
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    localpart = address.split("@", 1)[0] if "@" in address else address

    def verdict(value: str, reason: str, signal: str) -> dict[str, str]:
        return {"verdict": value, "reason": reason, "signal": signal, "sender": address}

    if address and address in vip_senders:
        return verdict("important", "VIP sender wins before filtering", "vip_sender")
    if domain and domain in vip_domains:
        return verdict("important", "VIP domain wins before filtering", "vip_domain")

    content_type = headers.get("content-type", "").lower()
    if "text/calendar" in content_type or any(
        name.startswith("x-microsoft-cdo") for name in headers
    ):
        return verdict("important", "calendar message", "calendar_header")
    if headers.get("list-unsubscribe"):
        return verdict("promotional", "bulk sender self-identifies", "list_unsubscribe")
    precedence = headers.get("precedence", "").strip().lower()
    if precedence in {"bulk", "list", "junk"}:
        return verdict("promotional", f"Precedence: {precedence}", "precedence")
    if headers.get("list-id") and not headers.get("in-reply-to"):
        return verdict("promotional", "mailing-list broadcast", "list_id")
    campaign = sorted(CAMPAIGN_HEADERS.intersection(headers))
    if campaign:
        return verdict("automated", "delivery platform header, content still unknown", campaign[0])
    if localpart and PROMOTIONAL_LOCALPART.search(localpart):
        return verdict("automated", "non-conversational sender, content still unknown", "sender_localpart")
    return verdict("ambiguous", "no deterministic header match", "none")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("rfc822", "gog-thread-json"), default="rfc822")
    parser.add_argument("--vip-sender", action="append", default=[])
    parser.add_argument("--vip-domain", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    raw = sys.stdin.read()
    try:
        headers = (
            parse_gog_thread_json(raw)
            if args.format == "gog-thread-json"
            else parse_rfc822_headers(raw)
        )
        result = classify(headers, set(args.vip_sender), set(args.vip_domain))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        result = {
            "verdict": "error",
            "reason": str(exc),
            "signal": "parse_error",
            "sender": "",
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
