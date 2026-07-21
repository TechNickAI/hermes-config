---
name: keep-going
description: >
  Invoked as /keep_going (or /keep-going) when the agent has stopped short — asked
  "which option?", narrated a plan instead of executing it, or claimed a blocker that
  is not real — while the user has already given clear direction. Nudges the agent to
  re-anchor on the ORIGINAL request, pick a decisive default, and chain actions until
  the work is verifiably done, stopping only for a blocker it can prove. Use when the
  user says "keep going", "continue", "you got stuck, unstuck yourself", "why did you
  stop", or runs the /keep_going slash command.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [workflow, continuation, autonomy, anti-premature-stop, nudge]
---

# keep-going

You stopped, but the user's **original request is not verifiably done**. The user ran
`/keep_going` because your last stopping point was almost certainly manufactured — a
hallucinated blocker, an unnecessary "which option?", or a plan you narrated instead of
executing. **Treat your previous stopping reason as wrong unless you can prove
otherwise.** The burden of proof is on stopping, not on continuing.

Do this now:

1. **Re-anchor on the real finish line.** Re-read the whole thread. In one line,
   restate the user's *original* end goal — the actual finish line, not the nearest
   subtask you just completed. Where your prior conclusion conflicts with that goal,
   discard it: the user's instructions outrank your own last message.

2. **Act in THIS turn — do not stop after restating.** Identify the highest-value next
   **reversible** action and execute it right now. Then chain actions, one after
   another, until the *original* request is complete. Do not hand back a plan; run it.

3. **Decisive default beats a question.** Ambiguity, multiple valid paths, wanting
   confirmation, or "I should check first" are **not** blockers when the user has
   already given direction. Pick the best reasonable default and proceed. If a choice
   is genuinely consequential and reversible, make it, then note the assumption you
   made so the user can correct it — do not stall on it.

4. **Stop only for a blocker you can PROVE.** A real blocker is exactly one of:
   - **(a)** Credentials, permissions, or access you cannot obtain yourself.
   - **(b)** An irreversible or externally-visible action that needs approval — spending
     money, sending an external message, a hard delete, a production write, anything
     hard to take back. The approval gate is load-bearing; keep it.
   - **(c)** A required dependency you have *verified* is actually down (you tried it and
     it failed — not "the API might be unavailable").

   If and only if one of these holds, stop and state three things in one line each: the
   blocker, the evidence it is real, and the single thing you need to clear it.

5. **Finish, then report with evidence.** Otherwise, keep going until the original
   request is done, then report what you *actually accomplished* — with real tool
   output, file paths, or verifiable results, not assertions that it worked.

Do not acknowledge this nudge and stop. The correct response to `/keep_going` is almost
always the next action, executed now.
