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
set -o pipefail
curl -sS --fail-with-body -X POST https://api.vapi.ai/phone-number \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "vapi", "name": "<assistant name>"}' > phone-created.json \
  || { echo "provisioning failed:"; cat phone-created.json; rm -f phone-created.json; exit 1; }

jq -e '.id and .number' phone-created.json >/dev/null \
  || { echo "no id/number in response — see the half-provisioned record warning"; exit 1; }
```

This is a purchase, so it gets the same failure handling as assistant create: a billing
or inventory error returns 4xx with a JSON body, and plain `curl -sS` would exit 0 and
let setup continue as if a number existed. The `jq -e` gate additionally catches the
half-provisioned record described below, where Vapi returns a record with neither
`number` nor `providerResourceId`.

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
    "eotTimeoutMs": 1200,
    "eotThreshold": 0.55,
    "confidenceThreshold": 0.15,
  },

  "maxDurationSeconds": 900,
  "backgroundSound": "off",
  "endCallMessage": "Talk soon. Bye.",
  "endCallPhrases": ["goodbye", "talk to you later", "bye bye"],

  "startSpeakingPlan": {
    "waitSeconds": 0.2,
    "smartEndpointingPlan": { "provider": "vapi" },
  },
  "stopSpeakingPlan": { "numWords": 0, "voiceSeconds": 0.15, "backoffSeconds": 0.8 },

  "analysisPlan": { "summaryPlan": { "enabled": true } },
  "artifactPlan": { "recordingEnabled": true, "transcriptPlan": { "enabled": true } },
  "monitorPlan": { "listenEnabled": true, "controlEnabled": true },
}
```

Why these values:

| Field                 | Reason                                                                   |
| --------------------- | ------------------------------------------------------------------------ |
| `messages[]`          | Where the prompt actually lands. `systemPrompt` is accepted and ignored. |
| `maxTokens: 400`      | Hard ceiling on monologue length. Long turns feel awful on a phone.      |
| `endCall` tool        | Without it the assistant literally cannot hang up.                       |
| `flux-general-en`     | Nova-3 accuracy plus model-native end-of-turn detection in one model.    |
| `eotTimeoutMs: 1200`  | **The single biggest latency lever.** See below — the default is brutal. |
| `confidenceThreshold` | Default 0.4 silently DISCARDS low-confidence speech. See below.          |
| `maxDurationSeconds`  | Stops a wedged call from billing forever. Default is 600s.               |
| `stopSpeakingPlan`    | `numWords: 0` reacts to any sound instead of waiting for two words.      |
| `analysisPlan`        | Gives you a post-call summary without re-reading the transcript.         |
| `transcriptPlan`      | The transcript is the only honest record of what happened.               |
| `monitorPlan.control` | Without it you cannot end a live call.                                   |

### The two defaults that will ruin your first call

Both of these were found on a real test call, not read off a docs page.

**`eotTimeoutMs` defaults to 5000.** Flux waits a full five seconds of silence before
declaring a turn over whenever end-of-turn confidence stays under `eotThreshold`. On a
live call this reads as the assistant being slow or checked out — measured gaps of
4.1–5.6 seconds before it started speaking, none of which were the model thinking.
Dropping it to ~1200ms with `eotThreshold` at 0.55 is what makes the conversation feel
responsive. `startSpeakingPlan.waitSeconds` is a rounding error next to this.

**`confidenceThreshold` defaults to 0.4, and it discards rather than flags.** Anything
scoring below it never reaches the model at all. Isolated letters and digits score low,
so a caller spelling something out loses characters silently — on the test call, five
letters of the alphabet vanished mid-sequence and the assistant read back the remainder
as if it were complete. Drop it to ~0.15 for anything involving spelling, digits, or
read-back confirmation.

`stopSpeakingPlan.voiceSeconds` only applies when `numWords` is `0`; setting both makes
one of them dead config.

**Silence handling.** `silenceTimeoutSeconds` is documented on Vapi's call-timeout page
and the API accepts it, but it is absent from the current assistant schema, so do not
count on it. `maxDurationSeconds` is the reliable hard stop. If you want re-prompting on
dead air, use the idle-message/hooks mechanism and confirm it on a real call.

### Pick the model from the live enum, not from memory

Do not write a model ID from recall. An LLM's sense of "the current model" is frozen at
its training cutoff, so the confident-sounding answer is reliably a version or two stale
— and a stale-but-valid ID fails silently, since it is still accepted and still works.
Nobody notices except the person paying for a worse assistant.

Vapi publishes the exact accepted values. Read them:

```bash
curl -sS https://api.vapi.ai/api-json \
  | jq -r '.components.schemas.AnthropicModel.properties.model.enum[]'
```

Swap `AnthropicModel` for `OpenAIModel`, `GroqModel`, and so on. Take the newest entry
for the family you want, and confirm the transcriber the same way
(`.components.schemas.DeepgramTranscriber.properties.model.enum[]`).

This costs one request. Do it every time you create or update an assistant.

### Creating it

```bash
set -o pipefail
curl -sS --fail-with-body -X POST https://api.vapi.ai/assistant \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d @assistant.json > assistant-created.json \
  || { echo "create failed:"; cat assistant-created.json; rm -f assistant-created.json; exit 1; }

ASSISTANT_ID=$(jq -er '.id' assistant-created.json) || { echo "no id in response"; exit 1; }
echo "$ASSISTANT_ID"
```

Three things are doing work here, and dropping any one of them lets a failed create look
like a successful one: `--fail-with-body` makes curl exit non-zero on a 4xx/5xx while
still showing you the error body; the `||` branch discards the file so an error response
never masquerades as an assistant; and `jq -er` fails loudly when `.id` is absent
instead of quietly printing `null`. Piping curl straight into jq would report jq's exit
status and swallow curl's failure entirely.

Confirm the printed `id` is a real UUID, then read it back before trusting it. Export it
first so the rest of this document — and a later session that did not run the create —
both refer to the same assistant:

```bash
export VAPI_ASSISTANT_ID="$ASSISTANT_ID"

curl -sS --fail-with-body -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/assistant/$VAPI_ASSISTANT_ID" \
  | jq '{model: .model.model, prompt_chars: (.model.messages[0].content | length),
         tools: [.model.tools[].type], transcriber: .transcriber.model,
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
5. **Interruptions** — stop the moment the person starts talking. **Never restart or
   repeat a point after being cut off**; answer what they said and move forward. A real
   test call failed on exactly this: the assistant was interrupted, then replayed its
   entire previous turn. Repeating yourself after an interruption is worse than the
   interruption.
6. **Reading things back** — read back exactly what was heard, including any gap. "I got
   H I J K, then it cut out before Q" is the correct behavior. Never quietly fill a gap
   or guess at a letter or digit; a confident wrong read-back is the worst outcome.
7. **Identity and honesty** — say plainly that you are an AI assistant if asked. Never
   guess at facts, dates, or numbers. Never promise, approve, or commit on anyone's
   behalf beyond what the call's TASK block authorizes.
8. **Opening a call** — say who you are, who you are calling for, and why, in the first
   fifteen seconds. Get to the point.
9. **Voicemail and wrong numbers** — leave a short message and end; if it is the wrong
   person, apologize, disclose nothing, and end.
10. **Ending** — one sentence, then the `endCall` tool. Do not linger.
11. **IVR navigation** — use the `dtmf` tool for digits, never speak them aloud; listen
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

Back up first — there is no version history in the API. Write to a temp file and only
promote it once you have confirmed it is a real assistant, so a 401 or a truncated
transfer cannot leave you holding an error body as your only rollback copy:

```bash
BACKUP="assistant-backup-$(date +%F).json"
curl -sS --fail-with-body -H "Authorization: Bearer $VAPI_API_KEY" \
  "https://api.vapi.ai/assistant/$VAPI_ASSISTANT_ID" > "$BACKUP.tmp" \
  || { echo "backup failed:"; cat "$BACKUP.tmp"; rm -f "$BACKUP.tmp"; exit 1; }

jq -e '.id and .model' "$BACKUP.tmp" >/dev/null \
  || { echo "backup is not a valid assistant, refusing to patch"; rm -f "$BACKUP.tmp"; exit 1; }

mv "$BACKUP.tmp" "$BACKUP"
```

Do not proceed to PATCH until that file exists. The patch is the irreversible step; the
backup is the only thing standing behind it.

Then `PATCH` **one field group at a time** (model, transcriber, call controls, speaking
plans, analysis). Vapi rejects the whole request for a single bad field, so a grouped
patch tells you precisely which group was refused instead of failing opaquely.

Finish with a `GET` readback and diff it against what you intended. A `200` means
accepted, not applied as you imagined — and Vapi will echo back fields it does not
actually implement, so check behavior on a real call when it matters.

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
