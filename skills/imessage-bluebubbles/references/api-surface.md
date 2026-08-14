# BlueBubbles API surface (verified)

Read directly from `gateway/platforms/bluebubbles.py` in the installed Hermes v0.20.0
tree. These are the endpoints the Hermes adapter actually calls, so they are the
supported set — not a copy of upstream docs.

## Auth

Password goes in the **query string**, URL-encoded:

```
{server_url}{path}?password={quote(password)}
```

The BlueBubbles webhook registration API cannot send custom headers, which is why the
adapter carries credentials this way rather than in an Authorization header. Do not
"fix" this to a header.

## Endpoints

| Method          | Path                                    | Purpose                                 |
| --------------- | --------------------------------------- | --------------------------------------- |
| GET             | `/api/v1/ping`                          | liveness + password check               |
| GET             | `/api/v1/server/info`                   | version, os_version, `private_api` flag |
| POST            | `/api/v1/chat/query`                    | list chats — **the real FDA test**      |
| POST            | `/api/v1/chat/new`                      | create a chat                           |
| GET             | `/api/v1/chat/{guid}?with=participants` | chat detail                             |
| GET             | `/api/v1/chat/{guid}/message`           | message history                         |
| POST            | `/api/v1/message/text`                  | send text                               |
| POST            | `/api/v1/message/attachment`            | send a file (multipart)                 |
| POST            | `/api/v1/chat/{guid}/typing`            | typing indicator on                     |
| DELETE          | `/api/v1/chat/{guid}/typing`            | typing indicator off                    |
| POST            | `/api/v1/chat/{guid}/read`              | mark read                               |
| GET             | `/api/v1/attachment/{guid}/download`    | fetch inbound attachment                |
| GET/POST/DELETE | `/api/v1/webhook`                       | register / list / remove webhooks       |

`{guid}` must be URL-encoded (`urllib.parse.quote(guid, safe="")`) — GUIDs contain `;`
and `+`.

## Send payload

```json
{
  "chatGuid": "iMessage;-;+15551234567",
  "message": "text body",
  "tempGuid": "<unique per send>",
  "method": "apple-script"
}
```

`tempGuid` must be unique per send — it is the idempotency/correlation key.

`method` is `"apple-script"` by default. The adapter switches it to `"private-api"` only
when `/api/v1/server/info` reported `private_api: true`. Requesting `private-api` when
the helper is not connected fails the send, so gate on the server-reported flag rather
than assuming.

## Chat GUID format

```
iMessage;-;+15551234567     1:1 by phone
iMessage;-;user@icloud.com  1:1 by email
iMessage;+;chat<id>         group chat
SMS;-;+15551234567          green-bubble SMS
```

## Attachments and message text

Attachments arrive through the REST API, **not** in the webhook payload — the webhook
carries small JSON events only (the adapter caps the body at 1 MiB). Fetch via
`/api/v1/attachment/{guid}/download`.

Message bodies come back populated because BlueBubbles decodes `attributedBody`
server-side. This is the key difference from raw `sqlite3` against `chat.db`, where
`m.text` is NULL on modern macOS and every row looks like `[attachment/reaction]`.

## Webhook

The adapter runs its own aiohttp listener (default `127.0.0.1:8645`, path
`/bluebubbles-webhook`), registers it with the server on start, and unregisters on stop.
Inbound auth rides in the query string for the same header limitation noted above.
