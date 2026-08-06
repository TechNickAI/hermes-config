# Vapi First-Time Setup

Everything needed to take an agent from no voice capability to a verified working
outbound call.

## 1. Account

Sign up at <https://dashboard.vapi.ai>. New accounts include trial credits.

## 2. API key

Dashboard → API Keys → copy the **private** key. Store it as `VAPI_API_KEY` in the
agent's environment. The public key is for browser/web-call SDKs only and cannot create
assistants or place calls.

## 3. Phone number

```bash
curl -sS -X POST https://api.vapi.ai/phone-number \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "vapi", "name": "<assistant name>"}'
```

Free Vapi numbers are US-only, capped per account, and land in `activating` status for
1–2 minutes. Do not attempt a call until `status` is `active`.

**Save both the `id` and the `number` from this response.** `GET /phone-number/<id>`
returns the number masked (`+165****8621`) and there is no way to unmask it via the API.

- `id` → `VAPI_PHONE_NUMBER_ID`
- `number` → `VAPI_PHONE_NUMBER`

For a number outside the US, or a number you already own, bring your own carrier
(Twilio, Telnyx, or SIP) instead.

## 4. Assistant

One assistant per persona. Task-specific behavior belongs in `assistantOverrides` at
call time, not in a duplicate assistant.

### Recommended baseline

```jsonc
{
  "name": "<assistant name>",
  "firstMessage": "Hi there, this is <assistant name>, an AI assistant.",
  "firstMessageMode": "assistant-speaks-first",

  "model": {
    "provider": "anthropic",
    "model": "<verify current id against provider docs>",
    "temperature": 0.5,
    "maxTokens": 400,
    "messages": [{ "role": "system", "content": "<see below>" }],
    "tools": [{ "type": "dtmf" }, { "type": "endCall" }],
  },

  "transcriber": {
    "provider": "deepgram",
    "model": "flux-general-en",
    "language": "en",
  },

  "maxDurationSeconds": 900,
  "backgroundSound": "off",
  "endCallMessage": "Talk soon. Bye.",
  "endCallPhrases": ["goodbye", "talk to you later", "bye bye"],

  "startSpeakingPlan": {
    "waitSeconds": 0.4,
    "smartEndpointingPlan": { "provider": "vapi" },
  },
  "stopSpeakingPlan": { "numWords": 2, "backoffSeconds": 1.0 },

  "analysisPlan": { "summaryPlan": { "enabled": true } },
  "artifactPlan": { "recordingEnabled": false, "transcriptPlan": { "enabled": true } },
  "monitorPlan": { "listenEnabled": true, "controlEnabled": true },
}
```

Why these values:

| Field                 | Reason                                                                       |
| --------------------- | ---------------------------------------------------------------------------- |
| `messages[]`          | Where the prompt actually lands. `systemPrompt` is accepted and ignored.     |
| `maxTokens: 400`      | Hard ceiling on monologue length. Long turns feel awful on a phone.          |
| `endCall` tool        | Without it the assistant literally cannot hang up.                           |
| `flux-general-en`     | Nova-3 accuracy plus model-native end-of-turn detection in one model.        |
| `maxDurationSeconds`  | Cost ceiling. Default is 600s; set it deliberately rather than inheriting.   |
| `stopSpeakingPlan`    | Two words of caller speech stops the assistant. Talking over people is rude. |
| `analysisPlan`        | Gives you a post-call summary without re-reading the transcript.             |
| `transcriptPlan`      | The transcript is the only honest record of what happened.                   |
| `recordingEnabled`    | **Off by default** — recording consent is jurisdictional. Opt in knowingly.  |
| `monitorPlan.control` | Without it you cannot end a live call the user wants stopped.                |

`stopSpeakingPlan.voiceSeconds` is deliberately omitted: it applies only when `numWords`
is `0`, so setting both makes one of them dead config.

**Silence handling.** `silenceTimeoutSeconds` is documented on Vapi's call-timeout page
and the API accepts it, but it is absent from the current assistant schema, so do not
count on it. `maxDurationSeconds` is the reliable hard stop. If you want re-prompting on
dead air, use the idle-message/hooks mechanism and confirm it on a real call.

Verify the model and transcriber names against Vapi's current provider docs before
creating the assistant. Both move faster than this document does.

### Creating it

```bash
curl -sS --fail-with-body -X POST https://api.vapi.ai/assistant \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d @assistant.json | tee assistant-created.json | jq -r '.id'
```

`--fail-with-body` is what stops you from saving an error response as if it were an
assistant. Confirm the printed `id` is a real UUID, then read it back before trusting
it:

```bash
curl -sS --fail-with-body -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/assistant/$VAPI_ASSISTANT_ID" \
  | jq '{model: .model.model, tools: [.model.tools[].type], transcriber: .transcriber.model,
         maxDurationSeconds, recording: .artifactPlan.recordingEnabled,
         control: .monitorPlan.controlEnabled}'
```

A `201` means Vapi accepted the request, not that every field applied the way you meant.

### Base system prompt

Keep it about identity and the medium. Nothing scenario-specific.

1. **Foundation / values** — whatever grounding the agent family shares.
2. **Personality** — two or three sentences of who this assistant is.
3. **"You are on a live phone call. Your task instructions will tell you who you are
   calling, why, and what to accomplish. Follow them."**
4. **Speaking on the phone** — the non-negotiable block:
   - No markdown, ever. It gets read aloud as noise.
   - One to three sentences per turn.
   - Speak numbers as a person says them ("four oh five", not "4:05").
   - Spell anything that must be written down, and confirm it back.
   - If you need a moment, say so out loud rather than going silent.
5. **Interruptions** — stop the moment the person starts talking; give two or three
   seconds of silence before prompting; offer to call back if they sound rushed.
6. **Identity and honesty** — never guess at facts, dates, or numbers. Never promise,
   approve, or commit on anyone's behalf beyond what the call's TASK block explicitly
   authorizes.
7. **Opening a call** — in the first fifteen seconds, unprompted: that you are an AI
   assistant, who you are calling for, why, and how long it will take. If the call is
   being recorded, say so here. Then explicitly offer them the chance to decline or
   reschedule.
8. **Voicemail and wrong numbers** — leave a short message and end; if it is the wrong
   person, apologize, disclose nothing, and end.
9. **Ending** — one sentence, then the `endCall` tool. Do not linger.
10. **IVR navigation** — use the `dtmf` tool for digits, never speak them aloud; listen
    to all options first; press 0 or say "representative" when nothing matches; wait for
    the system to respond after each tone.

### Voice

Vapi's built-in voices are included at no extra cost and are the right default. Audition
them with the "Talk" button in the assistant editor — there is no static preview audio.

ElevenLabs and other premium voices require a paid plan on that provider plus a
credential registered with Vapi. Manage those in the dashboard under provider keys —
`GET /credential` is not in the published OpenAPI and should not be treated as a stable
interface.

Free-tier ElevenLabs is blocked for real-time streaming regardless of the credential
(verify against current ElevenLabs terms).

Save the assistant `id` as `VAPI_ASSISTANT_ID`.

## 5. Updating an existing assistant

Back up first — there is no version history in the API:

```bash
curl -sS -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/assistant/$VAPI_ASSISTANT_ID" > assistant-backup-$(date +%F).json
```

Then `PATCH` **one field group at a time** (model, transcriber, call controls, speaking
plans, analysis). Vapi rejects the whole request for a single bad field, so a grouped
patch tells you precisely which group was refused instead of failing opaquely.

Finish with a `GET` readback and diff it against what you intended. A `200` means
accepted, not applied as you imagined.

## 6. Verify

Place one real call to your own phone before calling anyone else. Then confirm:

- The call reached `status: ended` with `endedReason: assistant-ended-call`.
- The transcript exists and reads like speech, with no markdown artifacts.
- The assistant stopped talking when you interrupted it.
- It hung up on its own rather than waiting out the duration cap.
- `cost` is in the range you expected.

A call that connects is not a call that works. Read the transcript.

## Fleet deployment

One Vapi account, shared billing. Each agent gets its own assistant (personality, voice,
prompt) and its own phone number under the same org, so the persona a caller hears
matches the agent they know.

Store `VAPI_ASSISTANT_ID` and `VAPI_PHONE_NUMBER_ID` per agent; share `VAPI_API_KEY`
across the org.
