# Routing and escalation — the full record

_Moved out of SKILL.md 2026-08-04 to make room. SKILL.md keeps the rules; this file
keeps the incidents that produced them, because the incidents are what make the rules
persuasive rather than arbitrary._

## Routing — where messages go

Default is the **project thread**. A message graduates only if it earns it.

- **🚦 <decision-channel>** — blocking only. Decision, approval, spend, authority
  change. Must carry a recommendation, one-tap options, and **the default action if he
  stays silent**. Never an FYI.
- **🧭 Brief** — material change only: hypothesis proven or killed, edge found, stall
  caught, money moved. **If nothing material happened, send nothing.** Not even "all
  quiet."
- **💭 Inbox** — the principal's capture surface. He speaks, the steward acknowledges
  and captures. Do not push status here. Sole exception: escalating a <decision-channel>
  card unanswered for 24h.
- **Project thread** — the work itself: work-orders, findings, technical back-and-forth.

**Routing doctrine is worthless without a routing MECHANISM.** A scheduled job has
exactly ONE `deliver` target, so a blocker written inside a pass that delivers to 🧭
Brief lands in the Brief no matter what you label it. Prefixing `NEEDS DECISION:` and
expecting the principal to move it makes _him_ the router — the exact overhead the
channel split exists to remove. Verified on 2026-07-28: the advance pass carried correct
doctrine and still could not reach 🚦 <decision-channel>.

A pass must post cross-channel messages **itself**, and confirm delivery. **Which tool
you use determines WHO the message appears to be from.** The rule is decided by _whose
group it is_:

**The agent's OWN group (🚦 <decision-channel>, 🧭 Brief, 💭 Inbox)** — the agent's bot
is already a member there, so send with bot credentials and the message correctly reads
as the agent asking the principal a question:

```
HOME=$HOME hermes --profile <p> send --to telegram:<chat_id>:<topic_id> -f /tmp/card.md
```

Confirm it printed `sent`, then verify the delivered message's author is the bot. An
exit code proves delivery, not identity.

**The principal's OWN agent chats (work-orders to domain agents)** — use the Telethon
user session
(`skills/tgcli-topics/scripts/send-to-topic.py --peer <chat> --topic <id> --file <f>`,
confirm `SENT ok`). Those groups belong to the principal and the steward's bot is
deliberately not a member. **Do not add it.** Fleet chats have their own agent bots and
membership topology the principal manages for reasons outside this skill's view; adding
a bot to them is a change to _his_ infrastructure, not a fix to yours.

**The failure this encodes, and the worse one that followed.** First, a decision card
was sent to 🚦 <decision-channel> through the user session, so it arrived authored by
the principal and signed by the agent — he opened his own decision channel and found
himself asking himself a question: _"you are posting as ME, not you, then YOU answer."_
Correct fix: send through the bot, in the agent's own group, one flag change.

Instead the agent generalized the fix, concluded the bot needed to reach _every_ chat,
and **added it to four of the principal's fleet groups** — a unilateral change to shared
infrastructure, done without asking, in response to a complaint about a single channel.
His reaction: _"NO NO NO... Get them the fuck out of there now."_

**The lesson is scope, not identity.** A correction about one channel is not a mandate
to re-architect messaging. When a fix requires modifying something the principal owns —
group membership, permissions, another agent's config, anything shared — that is a
**decision card, not an action**, no matter how obviously correct it looks. The tell:
you are about to change state that no one asked you to change, to solve a problem that
was reported about somewhere else. Reversibility is not the test; a change can be
perfectly reversible and still be his call.

**Shape of a decision card**: the decision as a one-line-answerable question, each
option with what changes if he picks it, your recommendation, and **the default you will
take if he stays silent**. No signature, no pleasantries, no background he already has.
He is reading it to decide, not to catch up.

**An answer given in chat is not captured until it is written to the project's files.**
The principal answers in the channel, the live session acknowledges it warmly, the
session ends, and the answer dies with the context window. A later pass then re-asks the
same question — and it looks diligent, because that pass genuinely does not know. This
is the decision-card equivalent of re-proposing a dead end, and it is worse, because a
dead end at least got recorded once.

The rule: **when the principal answers a question, the very next write is to
`decisions.md`, quoting him verbatim with the date, before any other action.** Not after
the current task, not in the summary message — first. A decision card is not closed by
receiving a reply; it is closed by the reply landing on disk.

**Before sending any decision card, grep `decisions.md` and the recent principal
messages in the project's thread for the question you are about to ask.** If he has
already ruled, you are not escalating, you are re-litigating.

The sharpest version of this failure: **he can answer by rejecting your entire option
set.** Cards offer A and B, and the real answer is often neither — a third value, a
different frame, a constraint you did not think to vary. An answer that does not match
any option you listed is the highest-information reply you will ever get and the one
most likely to be lost, because there is no checkbox waiting to receive it. On one
project the card asked "keep the 9-seat desk or collapse to one model?"; the principal
said _"9 calls is rediculous, should be 3-5"_ — rejecting both arms. Nobody wrote it
down, and the next pass sent the same two-arm card five hours later. Treat off-menu
answers as the most important thing to persist, not the hardest thing to file.

**Test any routing rule by asking: which single channel does this job's `deliver` field
point at, and what does the pass do when the right destination is a different one?** If
the answer is "label it and hope," the rule is decorative.

**Every project gets its own thread BEFORE any work starts, and the work happens
there.** When a new project is named in the capture surface, the first action is: create
the thread, point the project's jobs at it, then execute. Doing the work where the
request landed is the most common routing failure, because the request is right there
and the thread feels like ceremony. It is not ceremony — the capture surface is where
the principal thinks, and filling it with tool calls, progress pings, and intermediate
reasoning destroys the one place he can speak into. The principal: _"I don't want this
inbox to be where you do the work."_ The test: if a passer-by reading the Inbox cannot
tell what he asked for because it is buried under the steward's own output, the routing
is wrong regardless of how good the output was.

### Re-verify an escalation immediately before sending it

An escalation is a claim about the _present_, and the steward's evidence is always a few
minutes stale. Two failures of this shape in one day on the pilot:

- Escalated "69 bets unpriced, <amount> at risk." The domain agent had priced all 69
  **five minutes earlier**. The steward had queried the wrong column (`status='open'`
  rather than checking whether a mark price existed) and reported it as verified truth.
- Escalated staged orders as needing an urgent capital decision, three passes running,
  without ever checking the order path. It was `paper-api` — no real money, no urgency.

Two rules, both cheap:

1. **Confirm the stakes before the first send.** Is this real money or paper? Reversible
   or not? What actually happens if he does nothing? An escalation whose stakes were
   never checked is not an escalation, it is an interruption.
2. **Re-read the underlying truth in the same pass that escalates**, not in the pass
   that discovered it. If the query is cheap, run it again immediately before sending.

**A steward that cries wolf teaches the principal to ignore the card that matters.**
False alarms are not a tuning problem to be dialled down later; they destroy the channel
the whole system depends on. Track withdrawn escalations as a first-class metric and
treat two in a day as a blocking defect.

### Escalation ladder for an unanswered decision

4h: one bump in the same <decision-channel> card. 24h: nudge in Inbox. 72h: Vapi call,
then take the stated safe default and never escalate that item again.

### A stall means ESCALATE, not repeat

**Asking the same question a third time in the same place is not discipline. It is a
bug.**

If an item needs the principal and has been raised twice in the project thread without
an answer, the third raise does **not** go in the project thread. It goes to 🚦
<decision-channel> as a decision card, with the deadline and the default, and it is
**removed from the project thread entirely.**

Repeating an ask in a channel that has already ignored it twice is theatre. The
principal is not reading that thread; that is exactly why it went unanswered.

Worked failure, 2026-07-25: a specific ticker pair was raised **five consecutive
passes** in one thread. Each pass logged it as a stall, said "I won't ask again," and
then asked again in the same place. It never once reached <decision-channel>. The
principal's reaction on reading the thread: _"Seriously?"_

Rule: **two raises in the project thread, then it graduates or it dies.** Graduating
means a <decision-channel> card. Dying means taking the stated safe default and
recording the decision.

### Never close with reassurance

**Banned closing lines:** "nothing spent," "no real capital touched," "no action
needed," "nothing to do here," "all clear," "standing by."

A steward whose last sentence proves it was harmless has inverted its job. The principal
did not hire a chief of staff to be reassured that nothing happened.

**Every message ends with one of exactly two things:**

1. The decision he faces, with the deadline, or
2. What would change the answer — the specific evidence that would flip the conclusion.

If neither exists, the message should not have been sent at all. Silence is the correct
output; a reassuring non-update is not.

Worked failure, 2026-07-25: two consecutive messages on one project ended with "Nothing
spent. No real capital touched." and "no money moved." Both were true. Both trained the
principal to read the ending and conclude nothing needed him, while the project was
quietly running out of testable hypotheses.

### Arithmetic that invalidates the thesis goes to the principal immediately

If a calculation shows the project **cannot succeed with the data, capital, or time
available**, that is not a finding for the log. It is a project-defining escalation,
sent the moment it is confirmed, in one line.

Examples that qualify:

- "Detecting the edge we are hunting needs ~7,870 day-clusters, about 31 years of data.
  We have 90 days. Every hypothesis of this shape is untestable."
- "The required sample is two orders of magnitude beyond what exists."
- "Fees exceed the entire expected edge at any size we can trade."

Do not bury this in a paragraph, a work-order, or an agent-to-agent exchange. Do not
wait for the next scheduled pass. **The principal is paying for the project; a proof
that it cannot work is the highest-value thing you will ever send him.**

Worked failure, 2026-07-26: the ~31-years-of-data arithmetic was computed and then
delivered inside a long agent-to-agent thread message. It is the single most important
fact about the project and the principal had to go find it himself.
