# Programmatic iMessage landscape (researched 2026-08)

Why BlueBubbles won. Keep this so a future session does not redo the dive.

## The ground truth

**Apple has no public iMessage API.** No endpoint, no SDK, no bot registration, no
webhook system. Messages for Business is inbound-only, customer-initiated, and sends
gray business-chat bubbles — not real iMessage. Every working method is unofficial and
violates Apple's ToS to some degree.

Every route reduces to one of three shapes:

1. Self-hosted Mac bridge (BlueBubbles, AirMessage)
2. Managed cloud API on someone else's Mac farm
3. Protocol reverse-engineering (pypush lineage)

## Evaluated options

### BlueBubbles — CHOSEN

Open source, self-hosted on a Mac, REST API + webhooks. Largest community and
best-supported of the self-hosted bridges. Consistently described across independent
sources as the most practical DIY route when you already own a Mac. **Decisive factor:
Hermes ships a native adapter for it.** Cost: free. Requires: an always-on, signed-in
Mac.

### AirMessage — rejected

Same architecture as BlueBubbles (Mac relay), more polished UI, better security model.
Smaller community, fewer troubleshooting resources, and no Hermes adapter. Strictly
worse fit for identical infrastructure cost.

### Beeper — rejected (and the user asked about this one specifically)

Beeper Desktop API is real and genuinely good: local REST + WebSocket + MCP on
`localhost:23373`, official Python/JS/Go/PHP SDKs, bearer-token auth, a `beeper` CLI.
Two lines in Beeper's own documentation rule it out:

- **"iMessage is only supported on macOS"** — it still requires the Mac, so it buys
  nothing over BlueBubbles on the infrastructure axis.
- **"We recommend Beeper Desktop API for personal use only. Sending too many messages
  might result in account suspension by the networks."**

Net: an extra company and an extra account in the path, carrying suspension risk, for
zero architectural benefit. Worth knowing it exists; it is the right answer if someone
wants _many_ networks unified, not for hardening iMessage.

Note the trap: Beeper's marketing page for the Desktop API lists a dozen networks and
**omits iMessage entirely**. Only the docs state the macOS restriction. Check primary
docs, not the product page.

### Managed cloud APIs (Sendblue, LoopMessage, Blooio, MessageBlue, Photon) — rejected

Real products, REST + webhooks, no Mac required, SOC 2 / HIPAA on some tiers,
~$100+/month. Wrong shape for a personal assistant: they send from **their** number or a
shared line pool, not the owner's own iMessage identity. Correct choice for business/CRM
outbound, not for "my agent texts as me."

(Hermes does ship a Photon platform plugin at `plugins/platforms/photon/` — managed
iMessage over a gRPC sidecar — if a shared-line-pool model is ever wanted.)

### pypush and protocol reverse-engineering — rejected

Reimplements Apple's APNs/iMessage protocol; no Mac required in principle. Currently
mid-rewrite and not feature-complete: basic text only, no attachments, groups, or
reactions. Requires Apple device validation data (serials, disk UUIDs). Legal exposure
beyond ToS (DMCA/CFAA argued in multiple sources).

**The cautionary precedent:** Beeper Mini shipped this approach on Android in late 2023
and Apple killed it within roughly two weeks by rotating authentication keys, then
blocked each successive workaround. Any protocol-level solution is one Apple server-side
change from dead. Research only.

## The honest summary

There is no clean path, and every source says so. The Mac-bridge route is what serious
people run. If you already own the Mac, BlueBubbles is the answer — and with a native
Hermes adapter it is config, not construction.
