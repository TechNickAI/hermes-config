---
name: imessage-bluebubbles
description: >
  Use when sending, reading, or searching iMessages from an agent on macOS, or when
  setting up, hardening, or debugging the BlueBubbles iMessage bridge. Also use when an
  existing chat.db/imsg approach breaks with permissionDenied, "authorization denied
  (code 23)", or hangs with no output after a macOS or Python upgrade.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [imessage, bluebubbles, apple, messaging, bridge, tcc]
    related_skills: [multi-review]
    requires:
      - macOS with Messages.app signed into iMessage
      - BlueBubbles server app (installed by scripts/setup-bluebubbles.sh)
      - Full Disk Access granted by hand (macOS permission prompts cannot be scripted)
      - python3 with the requests package
---

# iMessage via BlueBubbles

Use for ANY iMessage work on a Hermes fleet Mac -- sending, reading, searching messages,
or standing up the bridge itself. Also the right skill when iMessage is broken: `imsg`
failures (`permissionDenied`, `authorization denied (code 23)`, hangs with no output)
are the signal to move to this bridge.

## Why this exists

The older `imsg` CLI reads Apple's `~/Library/Messages/chat.db` directly. macOS governs
that file with TCC, and **TCC grants Full Disk Access to an exact interpreter path**. So
a routine `uv` Python patch bump silently revokes access and every scheduled iMessage
job dies with `authorization denied (code 23)` -- no Hermes version change, no config
change, no log warning. That happened on a-contact a real incident. Add the sandboxd
wedge (`imsg` hangs forever, zero output) and direct-database access is structurally
fragile.

BlueBubbles moves the Apple-facing half into one long-running signed app that holds its
own permission grant. Hermes talks to it over loopback HTTP. **Python never touches
chat.db again, so a Python upgrade can no longer break iMessage.** That is the whole
point -- a structural fix, not a workaround.

Hermes ships a first-class BlueBubbles platform adapter
(`gateway/platforms/bluebubbles.py`, registered as `Platform.BLUEBUBBLES`), so the agent
can _receive_ iMessages as a real channel, same as Telegram.

## Two separate capabilities -- do not confuse them

| Capability          | Mechanism                                                     | Use for                                                 |
| ------------------- | ------------------------------------------------------------- | ------------------------------------------------------- |
| **Inbound channel** | Hermes gateway adapter, `BLUEBUBBLES_*` env, webhook on :8645 | People messaging the agent and it replying in-thread    |
| **Outbound tool**   | `scripts/bb.py` calling the REST API                          | The agent sending or reading messages as part of a task |

Setting the env vars enables the _channel_. `bb.py` works independently and needs only
the same two credentials.

## Setup

```bash
./scripts/setup-bluebubbles.sh              # full guided install
./scripts/setup-bluebubbles.sh --verify     # health-check an existing install
./scripts/setup-bluebubbles.sh --exposure   # check for public tunnel exposure
./scripts/setup-bluebubbles.sh --wire       # write BLUEBUBBLES_* to .env only
```

### What cannot be automated, ever

macOS protects the TCC database with SIP; it is unreadable and unwritable even under
`sudo`. **Permission grants are a human click.** Do not attempt to script them, and do
not claim setup is done until a real API call proves access. Budget a screenshare for a
remote fleet member.

The human steps are exactly four:

1. Set a **server password** in BlueBubbles' first-run wizard.
2. Grant **Full Disk Access** to BlueBubbles when macOS prompts.
3. Grant **Automation > Messages** to BlueBubbles. Reading works without this; **sending
   hangs forever without it.** See `references/send-path-diagnosis.md`.
4. Turn the **public tunnel off** (see Hardening).

Steps 2 and 3 are different permissions with different failure modes. Full Disk Access
governs reading `chat.db`; Automation governs driving Messages.app to send. A bridge
that reads perfectly can still be unable to send a single message, so verify both before
declaring the bridge working.

### Skip the Google / Firebase sign-in -- Hermes does not use it

First-run setup pushes a Google sign-in. That is **Firebase Cloud Messaging**, which
exists so the BlueBubbles _phone app_ receives push notifications. The Hermes adapter
receives messages by **webhook over loopback** (`127.0.0.1:8645`), so FCM is never
consulted.

Skip it. If a fleet owner gets stuck on Google auth, that is not a blocker -- it is an
optional step for a client we do not run. The same reasoning applies to the Cloudflare
tunnel (see Hardening): both exist for remote phone clients.

the operator hit exactly this on a real incident and it stalled the install for no
reason.

### Full Disk Access will NOT be in the list until the app asks for it

macOS populates the Full Disk Access list **on demand** -- an app appears only after it
first attempts to read a protected file. A half-configured BlueBubbles has not tried to
open `chat.db` yet, so it is genuinely absent from System Settings, and the user has not
missed anything.

The order is therefore the reverse of what the setup instructions imply:

1. Finish first-run setup (server password set)
2. BlueBubbles attempts to read `chat.db`
3. **Then** it appears under Full Disk Access
4. Enable it, then **restart BlueBubbles** -- TCC is evaluated at process start, so a
   running app never picks up a new grant

Do not send someone hunting through Settings for an entry that cannot exist yet. Confirm
what is actually granted by reading TCC directly:

```bash
sudo sqlite3 "/Library/Application Support/com.apple.TCC/TCC.db" \
  "select client, service, auth_value from access where client like '%luebubble%';"
```

`auth_value` 2 = granted, 0 = denied, no row = never requested.
`kTCCServiceAccessibility` is the Private API grant (tapbacks, typing);
`kTCCServiceSystemPolicyAllFiles` is Full Disk Access. They are separate -- having
Accessibility does not imply FDA.

**But do not treat a missing FDA row as proof of no access.** Verified on the operator's
Mac Studio a real incident: BlueBubbles returned the full chat list with **no
`kTCCServiceSystemPolicyAllFiles` entry at all**, only Accessibility. An Electron app
launched from an already-authorized parent can inherit disk access, so the TCC table
under-reports. The authoritative test is always a real `chat/query` call:

```bash
./scripts/bb.py health      # 'chat access: ok' is the only proof that counts
```

Read the TCC table to explain _why_ something is broken; never to conclude that
something works or does not.

### Gatekeeper: the app refuses to launch until you clear quarantine

BlueBubbles is signed with a Developer ID but **not notarized by Apple**. macOS tags
every download with `com.apple.quarantine`, and on an un-notarized app that tag makes it
refuse to open (often as a misleading "damaged" message). The fix is one command, and
`setup-bluebubbles.sh` now does it automatically:

```bash
xattr -dr com.apple.quarantine /Applications/BlueBubbles.app
```

This clears the download tag on **one bundle only**. It does not weaken Gatekeeper
system-wide. Verified in practice -- the app launched immediately afterward.

Two follow-on facts:

- `spctl -a /Applications/BlueBubbles.app` still reports **rejected / Unnotarized
  Developer ID** after the tag is cleared. Expected, and does **not** block launch. Do
  not chase it.
- GUI fallback: **System Settings > Privacy & Security**, scroll down, **Open Anyway**.

### The Homebrew cask is deprecated -- prefer the DMG for fleet rollout

`brew install --cask bluebubbles` works today but is **deprecated for failing the
Gatekeeper check and is disabled 2026-09-01**. Convenient because it is scriptable, but
not the durable path. For fleet rollout use the DMG from
<https://bluebubbles.app/downloads/> plus the quarantine-clear above.

### SIP is optional -- default to leaving it ON

| SIP                   | What works                                    | Who                                     |
| --------------------- | --------------------------------------------- | --------------------------------------- |
| **Enabled** (default) | Send, receive, attachments, read receipts     | Non-technical owners. **Recommended.**  |
| **Disabled**          | Adds tapbacks, typing indicators, edit/unsend | Technical hosts already running SIP-off |

Sending and receiving do **not** need SIP disabled. Only the Private API extras do.
Never talk a non-technical owner through disabling SIP for typing indicators -- not
worth it, and on Apple Silicon it also disables running iOS apps. Prior fleet notes
recording "BlueBubbles needs SIP disabled + ~2hr of pain" describe the Private API tier,
not the base install.

## Hardening: turn off the public tunnel

**BlueBubbles opens a public Cloudflare tunnel by default.** On first launch on the
operator's Mac it generated a live `trycloudflare.com` URL pointing at the box. That
exists so phone clients can reach the server from anywhere -- but **Hermes talks to it
over loopback and never needs it.** Left on, it is a public URL fronting the owner's
entire message history, guarded by one password.

Turn it off: **BlueBubbles > Settings > Connection**, set Proxy Service to **Dynamic
DNS** with address `http://localhost:1234`, or disable the tunnel.

### Turning it off headlessly, and the orphaned-process trap

Config is held **in memory** and flushed on shutdown (`set-config` / `config-update`
handlers in `app.asar`), so a direct DB write while the app runs gets clobbered. Correct
sequence:

```bash
# 1. quit cleanly so in-memory config is flushed
osascript -e 'tell application "BlueBubbles" to quit'
# 2. write the DB (values verified from the app bundle: dynamic-dns | lan-url |
#    cloudflare | ngrok)
sqlite3 ~/Library/Application\ Support/bluebubbles-server/config.db \
  "update config set value='dynamic-dns' where name='proxy_service';
   update config set value='http://localhost:1234' where name='server_address';"
# 3. relaunch and verify the values SURVIVED the restart
open -a BlueBubbles
```

**Then kill the orphaned tunnel.** Verified in practice: after the config change and a
full app restart, the bundled `cloudflared` was **still running from the previous
session** and the public URL still answered HTTP 200. The config was correct and the box
was still exposed.

```bash
pgrep -f "BlueBubbles.app.*cloudflared"     # BlueBubbles' own tunnel only
kill <pid>
```

Match on the **BlueBubbles.app path**, never on `cloudflared` alone -- the operator may
run their own unrelated tunnels on the same box (the operator does). Killing those is
collateral damage.

**Proof is a dead public URL, not a changed setting:**

```bash
curl -m 25 -o /dev/null -w "%{http_code}\n" "https://<old-url>/api/v1/ping"
# 530 or connection failure = actually closed. 200 = still exposed.
```

```bash
./scripts/setup-bluebubbles.sh --exposure   # reads the server's own config
```

Treat a public `server_address` on a fleet member as a finding to fix, not a note to
file.

## Daily use

`scripts/bb.py` is the agent-facing replacement for `imsg`. Plain-text output, one
record per line -- no JSON parsing.

```bash
./scripts/bb.py health                                   # server + auth + disk access
./scripts/bb.py chats --limit 20                         # recent conversations
./scripts/bb.py find --query "a-contact"                  # locate a chat
./scripts/bb.py history --chat "a-contact" --limit 30     # read a conversation
./scripts/bb.py send --chat "a-contact" --text "on my way"
```

`--chat` accepts a raw GUID or a fuzzy name/number. **On an ambiguous match it prints
the candidates and exits without sending** -- messaging the wrong person is not
recoverable, so it never guesses.

### Rules

1. Confirm recipient and content before sending on the user's behalf.
2. **Read the thread before sending to a recipient you resolved yourself.** Stored
   contact data conflicts and Apple Contacts genuinely attaches one number to multiple
   people. See `references/recipient-verification.md`.
3. Never message an unknown number without explicit approval.
4. Rate-limit yourself. No bulk sending.
5. Run `health` before diagnosing anything.
6. **Never retry a send after a timeout.** Re-read the thread to establish whether it
   landed -- a timeout is absence of information, not failure.

## Verification

`ping` is **not** sufficient -- it passes with no disk access at all. The real test is
`chat/query`, which reads the message database:

```bash
./scripts/bb.py health     # exercises ping, server/info, AND chat/query
```

`chat access: ok` is the only line that proves Full Disk Access works. A green ping with
empty chats means the app is running but blind.

## Verification

Run the suite. It exercises the bridge the way an agent actually uses it and reports
PASS/FAIL/SKIP per check.

```bash
./scripts/test-bluebubbles.py                      # read-only, safe, no messages
./scripts/test-bluebubbles.py --send-to "any;-;+15551234567" \
                              --from-name "YourAgent"
./scripts/test-bluebubbles.py --json               # machine-readable summary
```

Exit 0 = all passed, 1 = failures, 2 = could not run. 16 checks across seven areas:
process, auth, disk access, read path, safety guards, network exposure, Hermes wiring,
and (opt-in) a live send.

Design rules baked into the suite, each earned from a real failure:

- **Auth enforcement is tested with a WRONG password.** If a bad password is accepted,
  every other PASS is meaningless.
- **Disk access is tested with `chat/query`, never `ping`.** Ping succeeds with zero
  disk access.
- **Exposure is tested by fetching the public URL**, not by reading a config value.
  Config can say loopback while an orphaned tunnel still serves traffic.
- **A live send is confirmed by reading the message back from the server**, not by
  trusting the sender's own success claim.
- The send test is **opt-in and requires an explicit GUID**, because it texts a real
  human. Identify yourself in test messages.

Validate the suite itself periodically by pointing it at a wrong password and a wrong
port; both must FAIL. A suite that cannot fail proves nothing.

`bb.py health` remains the quick single check; `chat access: ok` is the only line that
proves Full Disk Access.

## Pitfalls

### A send that times out has NOT necessarily failed -- verify, never retry

The most dangerous failure mode in this skill. AppleScript sends routinely exceed a
20-30s HTTP timeout: the request blocks while Messages.app does the work, but **the
message still sends**. The client gives up; Apple does not.

Hit in practice." It **had** sent. She replied. the operator caught the error.

Two rules follow:

1. **A timeout means UNKNOWN, not failed.** Absence of information is not evidence of
   absence. Never report a send as failed on a timeout alone.
2. **Verify after a delay, and never retry blind.** An immediate re-read races the
   in-flight send and returns a false negative. Wait, re-read the thread, match on
   message text. Retrying instead of verifying **double-texts a real person** --
   unrecoverable.

`bb.py send` handles this: 120s timeout, then polls the thread for 30s looking for the
sent text, printing `CONFIRMED` or an explicit `UNCONFIRMED` that tells the operator to
check Messages.app before retrying.

By hand, the same discipline:

```bash
# WRONG -- races the send, reports a false negative
./bb.py send --chat X --text "hi"; ./bb.py history --chat X --limit 3

# RIGHT -- give a slow send time to land before concluding anything
./bb.py send --chat X --text "hi"; sleep 30; ./bb.py history --chat X --limit 3
```

### A hang is not proof of a missing permission

A hanging send looks exactly like a blocked Automation prompt, so it is tempting to
conclude the app lacks Automation access to Messages (`com.apple.MobileSMS`). On the
operator's Mac that inference was **wrong** -- sends worked with no such TCC grant; the
hang was pure latency, and the user was nearly sent to System Settings for nothing.

Distinguish by **error shape**: a missing permission or helper returns an **explicit
error** (`iMessage Private API Helper is not connected!`), while latency **hangs
silently**. Reads working tells you nothing about sends -- they use different mechanisms
-- but neither does a hang tell you a permission is missing.

### Name search returns nothing for someone you know exists

iMessage chats are keyed by phone number and email; `displayName` is `null` on most 1:1
threads. A name finding nothing is expected and is **not** evidence the person has no
thread. Search by number fragment, and paginate -- `chat/query` caps at 1000 rows per
call.

### `ping` succeeds but no chats come back

Full Disk Access is not granted. Grant FDA to **BlueBubbles.app**, then restart the app
-- TCC is evaluated at process start, so a running process never picks up a new grant.

### Password shows as set but auth still fails

Check the length, not just presence. First launch can leave an **empty** password that
still appears in config:

```bash
sqlite3 ~/Library/Application\ Support/bluebubbles-server/config.db \
  "select name, length(value) from config where name='password';"
```

Length 0 means first-run was never completed. Finish the wizard.

### `open -a BlueBubbles` hangs forever

The Mac is at the login screen. GUI apps cannot launch while locked, and the call blocks
rather than failing. Take a screenshot (`screencapture -x`) before assuming the app is
broken. Remote fleet Macs must be unlocked and logged in.

### Port 1234 already taken

Set `BB_PORT` and re-run. Preflight distinguishes "held by BlueBubbles" (fine) from
"held by something else" (fatal).

### Verifying over SSH proves nothing about scheduled jobs

Inherited from the `imsg` era, still applies to anything TCC-related: over SSH the
responsible process is `sshd`, which already holds Full Disk Access, so a probe can pass
while every launchd cron job is denied. **Only output from a job that fired on its own
schedule is authoritative.** BlueBubbles largely sidesteps this -- the grant belongs to
the always-running app, not to whatever interpreter invokes it -- which is precisely why
it is more robust than `imsg`.

## Migrating off `imsg`

Run both in parallel for a few days before retiring `imsg`. Keep the `imessage` skill
installed during the soak: it documents the sandboxd watchdog and the TCC diagnostic
ladder, still the right tools for a genuinely wedged Messages stack.

Retire `imsg` only after `bb.py health` passes from a real scheduled run, not from an
interactive shell.

## References

- `references/api-surface.md` -- BlueBubbles REST endpoints Hermes uses
- `references/landscape-2026.md` -- why BlueBubbles over Beeper, pypush, or a paid API
- `references/send-path-diagnosis.md` -- sends hang or 500 while reads work; Automation
  grant, Private API Helper, GUID format, pagination
- `references/recipient-verification.md` -- confirming you have the right person before
  an irreversible send
