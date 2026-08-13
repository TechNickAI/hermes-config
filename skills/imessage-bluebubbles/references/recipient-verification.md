# Verifying you have the right person before sending

Sending a message to the wrong person is **not recoverable**. It is the one failure in
this skill with real social cost, and it is easy to hit because contact data lies in
predictable ways.

Applies to any outbound message where the agent resolved the recipient itself, rather
than being handed an exact address by the user.

## A real near-miss

The operator asked for a warm personal message to a close contact. Resolution went:

1. Fuzzy name search returned **nothing** -- iMessage chats are keyed by phone number
   and email, and `displayName` is `null` for most 1:1 threads. A name search finding
   nothing says nothing about whether the person exists.
2. The knowledge base held **two different numbers** for her, recorded in two different
   sessions.
3. One of them carried a prior note that the address book had the **same number attached
   to two different people**.
4. Both numbers had live 1:1 threads. Both looked plausible.

Reading the last few messages of each settled it in seconds:

- One thread discussed **the intended recipient by name, in the third person**,
  alongside business scheduling. That is somebody else's thread.
- The other had shared personal history and matching subject matter. That is her.

Without that read, an affectionate message goes to a business contact.

## The rule

**Read the thread before sending to a resolved recipient.** Not the contact name, not
the notes -- the actual recent messages.

```bash
./scripts/bb.py history --chat "<candidate-guid>" --limit 10
```

Three signals distinguish a thread fast:

- **Third-person mentions.** A thread that discusses the target by name is almost never
  that person's thread. Strongest single signal.
- **Register.** Personal vs transactional tone.
- **Subject matter.** Does it match the relationship you believe you are in?

## Stored contact data is a lead, not an answer

Knowledge-base and address-book entries are **starting points to verify**, never
authority for an irreversible action:

- Two sessions can record two different numbers for one person; neither is marked wrong.
- Address books genuinely do attach one number to multiple people, which makes iMessage
  itself conflate them.
- People change numbers, and the old thread survives.

When stored sources disagree, that conflict is the finding. Resolve it against live
message content before acting -- do not average, do not pick the newer note, do not pick
the one that matches a name search.

## Never guess on ambiguity

`bb.py` prints candidates and exits when a selector matches more than one chat. This is
deliberate and must not be softened into "pick the best match". Verified live: the
selector `+1` matched **852** chats.

If the user's phrasing does not resolve to exactly one thread, ask. A question costs one
turn; a misdirected personal message cannot be taken back.

## Identify yourself in automated messages

When an agent sends on the operator's behalf as a test or an automated action, say so in
the message. A bare test string arriving from someone's personal number is confusing at
best. Name the agent and state that no reply is needed.

## Confirm the send landed

A send timeout is absence of information, not proof of failure. Re-read the thread
rather than retrying -- see `references/send-path-diagnosis.md`.

Report honestly which happened. "The message did not send, I verified by re-reading the
thread" is a complete and useful answer. Claiming a send succeeded because a command
returned is not.
