# Diagnosing the send path

Reading and sending use **different mechanisms** in BlueBubbles. Reads go through the
REST API against `chat.db`; sends drive Messages.app via AppleScript or the Private API
Helper. A working read path proves nothing about sends.

But the reverse inference is also invalid, and it is the one that bit us: **a hanging
send does not prove a missing permission.**

## Diagnose by error SHAPE, not by the fact that something stalled

| Symptom                                                    | Meaning                                                                                                             |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Send **hangs**, then times out                             | **Latency, most likely.** AppleScript sends are slow. The message probably SENT. Verify before concluding anything. |
| `iMessage Private API Helper is not connected!` (HTTP 500) | Helper Bundle not installed. Explicit, unambiguous.                                                                 |
| HTTP 401 on everything                                     | Server password wrong or empty                                                                                      |
| `chat/query` empty but ping OK                             | Full Disk Access problem, not a send problem                                                                        |

The governing rule: **an explicit error is information; a hang is not.** Missing
permissions and uninstalled helpers announce themselves with error text. Silence means
the operation is still in flight.

## What actually happened on a real incident (corrected)

An `apple-script` send to a contact hung until curl timed out at 90s with zero bytes
received. The initial diagnosis was that BlueBubbles lacked an Automation (AppleEvents)
grant for Messages (`com.apple.MobileSMS`), because the user-level TCC database showed a
grant for `com.apple.systemevents` but none for Messages.

**That diagnosis was wrong.** The message had already sent. a contact received it and
replied. the operator spotted it in the thread and challenged the report.

The corrected findings:

1. The send **succeeded**. The HTTP client gave up; Apple did not.
2. An immediate thread re-read raced the in-flight send and showed nothing, which was
   then misread as confirmation of failure.
3. The absent `com.apple.MobileSMS` TCC row was a **red herring**. Sends work on this
   box without it. the operator was nearly sent to System Settings to fix a non-problem.

Two general lessons, both worth more than the specific fix:

- **A timeout is absence of information, not evidence of failure.** Resolve it by
  observation, never by assumption in either direction.
- **Do not build a permissions theory from a stall.** The TCC table under-reports (an
  Electron app can inherit access from an authorized parent), so a missing row is weak
  evidence at best. Confirm a permission problem with an explicit error or a positive
  test -- never with a hang plus a missing row.

## The safe verification procedure

```bash
# Send, then WAIT before reading back. An immediate read races the send.
./bb.py send --chat "any;-;+15551234567" --text "hello"
sleep 30
./bb.py history --chat "any;-;+15551234567" --limit 5
```

`bb.py send` now does this automatically: 120s timeout, then polls the thread for 30s
matching on the sent text, and reports `CONFIRMED` or an explicit `UNCONFIRMED`.

**Never retry a timed-out send without verifying first.** Retrying an in-flight send
delivers the message twice to a real person, and that cannot be undone.

## If sends genuinely are blocked

Only after an **explicit** error, or after a verified send that never lands:

- Private API Helper: BlueBubbles > Settings > Private API > Install Helper Bundle.
  Requires SIP disabled. Gives faster sends plus tapbacks and typing indicators.
- Automation grant (only if a real prompt is being blocked): System Settings > Privacy &
  Security > Automation > BlueBubbles > Messages. Inspect the **user-level** TCC db,
  where Automation lives:

```bash
sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db \
  "select client, indirect_object_identifier, auth_value
   from access where service='kTCCServiceAppleEvents';"
```

Treat a missing row as a hypothesis to test, not a conclusion to act on.
