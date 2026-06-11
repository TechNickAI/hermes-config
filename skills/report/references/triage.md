# Fleet Bug Report Triage Runbook

When a bug card lands in the triage column from the `report` skill, here is how to work
it.

## Incoming card shape

```
title:      Short description the reporter provided, or the auto-summarized one
body:       Markdown with:
              - Reporter metadata (profile, platform, timestamp)
              - Context block (transcript excerpt if captured)
              - Reproduction info
tenant:     fleet-reports
status:     triage
created-by: the reporting user's name or platform handle
```

## Triage steps

1. **Read the card.** `hermes kanban show <id>`
2. **Classify.** One of:
   - **Bug** — something broke; agent or gateway behaving wrong
   - **Feedback** — friction or confusion, no breakage
   - **Working-as-intended** — close with explanation
   - **Duplicate** — link to the original with `kanban link`; archive this card
3. **Promote or close.**
   - Real bug → `hermes kanban specify <id>` to flesh out the spec, then assign to the
     right debugger profile
   - Feedback → add a comment with disposition, close as
     `done --result "feedback noted: ..."`
   - WAI → `hermes kanban complete <id> --result "working-as-intended: ..."`
4. **When fixed** — `hermes kanban complete <id> --result "fixed in <ref>"`. The
   reporter is automatically notified via the gateway's kanban notifier if they
   subscribed (Telegram/Discord/Slack sessions).

## Closed-loop notification

The `report` skill wires `kanban notify-subscribe` at card creation. When you call
`kanban complete`, the gateway notifier delivers:

```
✔ @<user> Kanban <id> done — <title>
```

directly to the reporter's chat/thread. No manual DM needed. If the card was re-triaged
or blocked, the reporter gets a `⏸` ping instead.

## Labels / tenants

All fleet bug reports land in `--tenant fleet-reports`. Use
`hermes kanban ls --tenant fleet-reports` for the full queue. Other tenants are
unrelated work streams; don't mix.

## Escalation

If a bug affects multiple fleet members or involves a Hermes upstream issue:

1. Comment on the kanban card with your analysis.
2. Raise with the maintainer before opening any upstream PR.
3. Never commit fleet-specific details (names, IPs, tokens) to a public repo.
