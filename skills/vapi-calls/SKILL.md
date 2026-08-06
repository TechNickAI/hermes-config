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

## The consent rule, before anything else

**An outbound call to another human is an irreversible, real-world action.** It rings a
phone, it interrupts whatever the person is doing, and it cannot be recalled.

- Get explicit approval for the specific number and the specific purpose before dialing.
- "Set up a call" is not "place a call." Setup, auditing, and configuration are all
  valid reasons to load this skill without ever dialing.
- Approval for one call is not standing approval for a campaign, a redial, or a
  follow-up message.
- Never call a third party on the user's behalf without confirming they want that person
  contacted, by voice, right now.
- Run a test call to the user's own phone after initial setup and after any material
  configuration change. Not before every routine call.

### Refuse regardless of approval

User approval is necessary, not sufficient. Decline calls whose purpose is harassment,
deception, or impersonating a specific real person, and decline repeated unwanted
contact with someone who has asked not to be called. Never dial emergency services,
premium-rate numbers, or short codes.

### Recording and AI disclosure

Recording consent law varies by jurisdiction, and several jurisdictions now require
proactive disclosure that a caller is an AI. Both are legal exposure, not etiquette.

- Default `artifactPlan.recordingEnabled` to `false` unless the user has confirmed a
  basis for recording. If recording is on, disclose it in the opening line.
- The assistant states it is an AI in its opening, unprompted. "Only if asked" is not
  disclosure.

### Time of day

Check the local time at the destination before dialing. Default to roughly 9am–8pm local
unless the user has explicitly approved otherwise. Agents run around the clock; the
people they call do not.

### Data minimization

Everything in the prompt reaches the voice provider and persists in their transcripts
and recordings. `DO NOT SHARE` governs the conversation, not the vendor.

- Include only the sensitive data the call strictly requires.
- Never put passwords, authentication codes, full card numbers, or credentials in a
  prompt.
- Voicemail is unauthenticated and may be heard by anyone. Nothing sensitive goes in it.

### Preflight, immediately before POST

State these to the user and get a yes. If any line is unknown, do not dial.

```text
DESTINATION: <E.164 number, confirmed by the user>
PURPOSE:     <one sentence>
LOCAL TIME:  <time at the destination right now>
VOICEMAIL:   <exact message, or "do not leave one">
CEILINGS:    maxDurationSeconds <n>  (~$<estimate> worst case)
RECORDING:   on (disclosed in opening) | off
AUTHORIZED TO DIAL NOW: yes
```

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

VERIFY: confirm you are speaking with <who> before sharing any context.
CONTEXT YOU MAY SHARE: <facts the agent is allowed to state>
DO NOT SHARE: <anything sensitive that must not leave the call>
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

### If call creation is ambiguous, do not retry

`POST /call` is not idempotent. A timeout or dropped connection may mean Vapi already
accepted the call and the phone is already ringing. Retrying places a second
unauthorized call to a real person.

On any uncertain response, reconcile before doing anything else:

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/call?limit=10" \
  | jq -r '.[] | "\(.createdAt) \(.id) \(.status) \(.customer.number)"'
```

If a call to that destination already exists, follow it — do not create another. If none
exists, get fresh approval before placing a replacement.

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

Keep going until `status` is `ended`. Stopping a call the user asked you to stop is
always authorized and never needs re-approval.

## After the call

Read `endedReason` first — it is the single most diagnostic field:

| `endedReason`             | Meaning                      | What to do                          |
| ------------------------- | ---------------------------- | ----------------------------------- |
| `assistant-ended-call`    | Wrapped up normally          | Read the summary and transcript     |
| `customer-ended-call`     | They hung up                 | Check the transcript for why        |
| `customer-did-not-answer` | No pickup                    | Report it. Nothing further.         |
| `voicemail`               | Machine picked up            | Confirm the message actually landed |
| `silence-timed-out`       | Dead air                     | Usually a bad connection            |
| `pipeline-error-*`        | Provider fault (STT/LLM/TTS) | Configuration or provider outage    |
| `exceeded-max-duration`   | Hit `maxDurationSeconds`     | Report it; ask before raising caps  |

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

**Any re-contact needs fresh approval.** That covers redialing after no answer, calling
back after a hang-up, and sending a text instead. Approval to call once was approval to
call once. Raising a duration or cost ceiling is also a new decision, not a retry.

## Cost

Roughly $0.05–0.15/min all-in, pay-as-you-go: Vapi's platform fee plus telephony plus
your own STT/LLM/TTS. Premium voices and frontier models sit at the top of that range,
built-in voices and small models at the bottom. `maxDurationSeconds` is your cost
ceiling — set it deliberately on every assistant.

Check current per-provider pricing at <https://vapi.ai/pricing> rather than trusting any
number written here.

## Pitfalls

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
