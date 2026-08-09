---
name: vapi-calls
description:
  Place and manage real outbound phone calls through the Vapi voice AI platform. Use
  when an agent needs to reach a human by phone — a reminder, a confirmation, a question
  for a business, an appointment booking, or any errand that a voice conversation
  handles better than a message. Covers first-time setup, per-call configuration, live
  call control, and reading the result afterward.
version: 1.0.0
license: MIT
metadata:
  hermes:
    requires:
      - "env: VAPI_API_KEY (Vapi dashboard → API Keys, private key)"
    tags: [vapi, voice, phone, outbound, telephony, calls]
    related_skills: []
---

# Vapi Voice Calls

Real outbound phone calls through Vapi. The agent writes the instructions, Vapi runs the
conversation, and you read the transcript afterward.

## When to load

- The user asks you to call someone, or to phone a business.
- A task needs a real-time back-and-forth with a human who is not on chat.
- You are setting up, auditing, or changing a voice assistant's configuration.

Do not reach for this when a text message would do. A phone call interrupts someone; a
message does not.

## Confirm the number and purpose before dialing

A call rings a real person's phone and cannot be recalled, so confirm you have the right
number and the right reason before you POST. "Set up a call" is not "place a call" —
setup, auditing, and configuration are all valid reasons to load this skill without ever
dialing.

## Prerequisites

Loading this skill needs only `VAPI_API_KEY`. Setup and auditing work with that alone.

**Placing a call** additionally needs a provisioned assistant and number:

```bash
echo "${VAPI_API_KEY:?missing}" >/dev/null
echo "${VAPI_ASSISTANT_ID:?run setup first}" >/dev/null
echo "${VAPI_PHONE_NUMBER_ID:?run setup first}" >/dev/null
```

If either is missing, work through [references/setup.md](references/setup.md) first.

## Placing a call

`POST https://api.vapi.ai/call`

```jsonc
{
  "phoneNumberId": "<VAPI_PHONE_NUMBER_ID>",
  "assistantId": "<VAPI_ASSISTANT_ID>",
  "customer": { "number": "+15551234567" }, // E.164, always
  "assistantOverrides": {
    "firstMessage": "Hi, this is <assistant name>, an AI assistant calling on behalf of <user>.",
    "variableValues": { "topic": "the thing this call is about" },
    "model": {
      "provider": "anthropic",
      "model": "<verify current id>",
      "messages": [
        {
          "role": "system",
          "content": "<base prompt>\n\nTASK: <everything about THIS call>",
        },
      ],
    },
  },
}
```

**The prompt goes in `model.messages[]`, not `model.systemPrompt`.** `systemPrompt`
appears in older examples across the web and the API will accept it and echo it back on
a `GET`, which makes it look like it worked — but it is not in the OpenAPI schema and
the model does not receive it. A convincing silent no-op. Always use
`messages: [{"role": "system", "content": "..."}]`.

The base assistant carries voice, transcriber, personality, and call controls. The
override carries what this one call is about.

**The voice agent has no memory between calls and cannot look anything up mid-call.**
Every fact it might need — names, dates, addresses, account numbers, the fallback if the
person says no — goes into the `TASK:` block. If it is not in the prompt, it does not
exist.

### Structure of a good TASK block

```text
TASK: Call <who> to <goal>.

VERIFY: confirm you are speaking with <who> before getting into it.
CONTEXT YOU MAY SHARE: <facts the agent is allowed to state>
DO NOT SHARE: <anything that must not leave the call>
WHAT SUCCESS LOOKS LIKE: <the one outcome that ends the call>
YOU MAY COMMIT TO: <the specific commitments authorized, or "nothing">
IF THEY SAY NO: <exact fallback>
IF YOU REACH VOICEMAIL: <leave this exact message, then end the call>
IF ASKED SOMETHING YOU DO NOT KNOW: say you will check and follow up. Never guess.
```

Explicitly listing "do not share" matters more than it looks. A voice agent with a warm
personality will volunteer context to be helpful unless told where the line is.

`YOU MAY COMMIT TO` is what makes booking an appointment possible without giving the
assistant open-ended authority. The base prompt forbids commitments; this line grants
back exactly the ones this call needs, and nothing else.

### Provisioning a number is a purchase, and there is no dry run

`POST /phone-number` buys a number the instant inventory exists. There is no search
endpoint, no availability lookup, and no dry-run flag anywhere in the API.

That matters because the error responses look like a free lookup and are not. Ask for an
area code with no stock and you get a helpful `400`:

```text
"This area code is currently not available. Hint: Try one of 562, 662, 716."
```

Ask for one WITH stock and the identical request silently succeeds and charges you. The
request that maps inventory and the request that spends money are the same request. Do
not loop over candidate area codes to "see what is available" — that is a purchase loop,
and it will drain the free-tier allowance (5 per account) in seconds.

Provision one number, read it back, stop. If the area code is empty, take the hint list
from the error and try exactly one more.

The free tier also gives you no choice of digits: `CreateVapiPhoneNumberDTO` accepts
`numberDesiredAreaCode` and nothing else. If a specific or memorable number matters, buy
it from a carrier that supports pattern search and import it via
`CreateTwilioPhoneNumberDTO`, which does take an explicit `number`.

A half-provisioned record is a real failure mode: if the purchase fails on billing, Vapi
can leave a record with `status: active`, a name, an `assistantId`, and **no `number`
and no `providerResourceId`**. It looks configured in a list view and cannot place a
call. Always confirm both fields are populated before trusting a number.

### If call creation is ambiguous, do not retry

`POST /call` is not idempotent. A timeout or dropped connection may mean Vapi already
accepted the call and the phone is already ringing. Retrying dials the person twice.

On any uncertain response, reconcile before doing anything else:

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/call?limit=10" \
  | jq -r '.[] | "\(.createdAt) \(.id) \(.status) \(.customer.number)"'
```

If a call to that destination already exists, follow it rather than creating another.

If none appears, **stop and ask the user before dialing again.** An absent record is not
proof that nothing is ringing: the call may not be listed yet, and a second `POST` on a
guess dials the person twice. The reconcile step exists to make the retry decision a
human's, not to authorize an automatic one.

## Watching a call in flight

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/call/$CALL_ID" | jq '{status, endedReason, cost}'
```

`status` moves `queued` → `ringing` → `in-progress` → `ended`, with `scheduled` before
and `forwarding` in the middle for some calls. Poll every few seconds, not in a tight
loop, and treat an unfamiliar status as "keep polling" rather than as a failure.

### Ending a call the user wants stopped

Wire this before you need it. The call object exposes `monitor.controlUrl` (present when
`monitorPlan.controlEnabled` is on — it defaults on, but set it explicitly so an audit
can see it). POST a control message to that URL:

```bash
CONTROL_URL=$(curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/call/$CALL_ID" | jq -r '.monitor.controlUrl')

curl -sS -X POST "$CONTROL_URL" \
  -H "Content-Type: application/json" \
  -d '{"type": "end-call"}'
```

Confirm the exact message `type` against the current live-call-control docs before
relying on it. Then verify, rather than assuming:

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/call/$CALL_ID" | jq '{status, endedReason}'
```

Keep going until `status` is `ended`.

## After the call

Read `endedReason` first — it is the single most diagnostic field:

| `endedReason`             | Meaning                      | What to do                          |
| ------------------------- | ---------------------------- | ----------------------------------- |
| `assistant-ended-call`    | Wrapped up normally          | Read the summary and transcript     |
| `customer-ended-call`     | They hung up                 | Check the transcript for why        |
| `customer-did-not-answer` | No pickup                    | Report it; do not auto-redial       |
| `voicemail`               | Machine picked up            | Confirm the message actually landed |
| `silence-timed-out`       | Dead air                     | Usually a bad connection            |
| `pipeline-error-*`        | Provider fault (STT/LLM/TTS) | Configuration or provider outage    |
| `exceeded-max-duration`   | Hit `maxDurationSeconds`     | Raise the cap or tighten the task   |

With `analysisPlan.summaryPlan.enabled` and `artifactPlan.transcriptPlan.enabled` on,
the ended call object carries the results — but **at the paths below, not where you
would guess.** There is no top-level `transcript`.

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/call/$CALL_ID" \
  | jq '{status, endedReason, cost,
         summary: .analysis.summary,
         transcript: .artifact.transcript}'
```

Fall back to `.artifact.messages` or `.messages` if the transcript is empty. **Report
the outcome from the transcript, never from the fact that the call connected.** A
completed call that failed its task is a failed call.

Do not auto-redial on no-answer. If the call needs to happen again, that is the user's
call to make.

## Cost

Roughly $0.05–0.15/min all-in, pay-as-you-go: Vapi's platform fee plus telephony plus
your own STT/LLM/TTS. Premium voices and frontier models sit at the top of that range,
built-in voices and small models at the bottom. A real 3.5-minute test call measured
$0.24, of which $0.165 was Vapi's own platform fee.

`maxDurationSeconds` is the ceiling that stops a wedged call from billing forever. Check
current per-provider pricing at <https://vapi.ai/pricing> rather than trusting any
number written here.

## Pitfalls

- **`POST /phone-number` is a purchase with no dry run.** The "area code not available"
  error looks like a free availability lookup, but the identical request against a code
  that HAS stock buys the number. Never loop area codes to probe inventory; it burns the
  5-per-account free allowance in seconds. One request, read it back, stop.
- **A stale model ID is the silent failure nobody catches.** Vapi accepts older model
  names indefinitely, so writing one from memory produces a working assistant that is
  quietly a generation behind. Read the accepted values from
  `.components.schemas.AnthropicModel.properties.model.enum` in
  <https://api.vapi.ai/api-json> every time, and never from recall.
- **`model.systemPrompt` is a silent no-op.** The API accepts it and returns it on
  `GET`, but it is not in the schema and never reaches the model. Use
  `model.messages[]`. This is the single most likely way to ship an assistant with no
  persona at all.
- **Cloudflare rejects some HTTP clients.** Python `urllib` gets
  `HTTP 403, error code: 1010` on every Vapi endpoint because of its default user-agent.
  `curl` and `requests` work. If you see 1010, it is the client, not your API key.
- **Acceptance is not application.** Vapi echoes back fields that are not in the OpenAPI
  schema (`backchannelingEnabled` and `silenceTimeoutSeconds` both round-trip on a `GET`
  while being absent from `CreateAssistantDTO`). A readback that shows your value is not
  proof the behavior is active. When it matters, verify against
  <https://api.vapi.ai/api-json> and confirm on a real call.
- **`GET /phone-number` masks the number** (`+165****8621`). Save the real number from
  the create response; you cannot read it back later.
- **PATCH field groups separately.** Vapi rejects the entire request for one bad field.
  Patching model, transcriber, and call-control groups in separate requests tells you
  exactly which field was refused.
- **Back up before you patch.** `GET /assistant/<id> > backup.json` first. There is no
  version history in the API.
- **Markdown gets read aloud.** Asterisks and bullets become audible noise. The base
  prompt must forbid markdown explicitly.
- **`201` on call creation means queued, not completed** — and check for `2xx`, not
  `== 200`, or you will treat a successful queue as a failure and double-dial.
- **`status` has more than four values.** `scheduled` and `forwarding` are real. Treat
  an unfamiliar status as "keep polling," never as failure.
- **`stopSpeakingPlan.voiceSeconds` only applies when `numWords` is 0.** Setting both
  means the second one is silently ignored. Pick one mechanism.
- **`maxDurationSeconds` defaults to 600s**, so a missing value is not unbounded cost.
  Still set it deliberately; just do not raise a false alarm when auditing.
- **Free-tier ElevenLabs is blocked** for real-time streaming. A paid plan plus a
  credential registered in Vapi is required for those voices.
- **One assistant per persona, not per task.** Task differences belong in
  `assistantOverrides`. Duplicating assistants means fixing every prompt bug N times.

## Auditing an existing setup

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" https://api.vapi.ai/assistant \
  | jq -r '.[] | "\(.id) \(.name) \(.model.model) \(.voice.voiceId)
      prompt_in_messages=\((.model.messages // []) | length > 0)
      legacy_systemPrompt=\(.model.systemPrompt != null)
      endCall=\([(.model.tools // [])[].type] | index("endCall") != null)
      control=\(.monitorPlan.controlEnabled)"'
```

Flag any assistant with: a prompt living in `systemPrompt` instead of `messages[]` (the
persona is not reaching the model), no `endCall` tool (it cannot hang up),
`monitorPlan.controlEnabled` off (you cannot stop a live call), a stale model, or a base
prompt with no phone-speech rules. See [references/setup.md](references/setup.md) for
the full recommended baseline.
