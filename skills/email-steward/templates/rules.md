# Email steward rules

## Account

- display name: `<account-name>`
- batch size: `25`
- maximum inbox-removal percentage per run: `40%`
- promotional action: `quarantine`
- alert delivery: inherited from the Hermes cron job

## Keep first

These rules win over every archive or quarantine signal.

### VIP senders

- `<person@example.com>`

### VIP domains

- `<company.example>`

### Subject patterns to keep

- `<pattern>`

## Flag rules

Use sparingly. A flag rule still must pass the actionability gate unless it concerns
account security or an explicit user request.

- sender/domain/subject: `<pattern>` reason: `<why this normally needs attention>`

## Archive rules

- sender/domain/subject: `<pattern>` reason: `<why this is a passive record>`

## Quarantine rules

- sender/domain/subject: `<pattern>` reason: `<why this is unwanted>`

## Never do automatically

- send or draft replies
- forward messages
- follow links
- open attachments
- permanently delete mail
- change account credentials, filters, or forwarding settings

## Confirmed corrections

Add only user-confirmed corrections here. Put observations and unresolved proposals in
`agent-notes.md`, not in permanent rules.
