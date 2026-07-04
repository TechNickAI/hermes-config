# Slow reviewer timeouts and router-path preservation

## Trigger

Use this reference when a multi-review panel appears to be degraded because cross-family
reviewer calls are slow or seem to time out.

## Lesson

A slow reviewer is not automatically a broken model path. Headless Hermes reviewer calls
(`hermes -z ... -t ''`) pay session/context startup (config load, memory prefetch, skill
scan, system-prompt build) before the model even reasons. Deep reviewers can take a
minute or more and still be perfectly healthy.

## Correct behavior

1. Keep reviewers on the configured Hermes/provider path. If the profile routes through
   a custom router (an OpenAI-compatible gateway or model router), let the reviewer call
   go through it like every other request.
2. Run model-family reviewers in parallel so wall time is the slowest single reviewer,
   not the sum of all of them.
3. Use generous timeouts: about 300s for normal reviews and 600s for deep/slow panels.
4. Stamp `degraded: single-model` only after a genuine failure or a blown 600s timeout —
   never because a reviewer was merely slow.
5. If the human says the path is "just slow," treat that as a scope correction. Do not
   keep debugging it as an outage or patch around it.

## Wrong behavior to avoid

Do **not** create a direct-endpoint "fast path" that bypasses the configured
provider/router just because direct calls seem faster. That silently changes the
architecture and policy surface (routing, keys, logging, rate limits), and it is not a
timeout fix. Only do it if the human explicitly approves that architecture.

## Why this rule exists

In a real session, a reviewer panel looked degraded because cross-family headless
one-shots were slow. A direct router-bypass "fast path" was added as a "fix," then
reverted once the maintainer pointed out it was a timeout issue, not a routing problem.
The durable fix was to preserve the configured provider/router path and update the
skill's timeout + parallel-wait guidance — not to build a side channel.
