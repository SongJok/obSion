# Immutable conversation context

## Purpose

An Obsion `Thread` is a durable problem-solving trajectory, not a UI-only message
list. Every ordinary `Run` therefore receives a bounded snapshot of the effective
conversation that existed before its `Turn` was created. The snapshot is a governed
run input alongside Agent, model, memory, Evidence, and capability versions.

Conversation context helps the model interpret follow-up language and prior intent.
It is never Evidence: factual claims in a new answer must still cite Evidence created
or attached to the current Run.

## Capture boundary

`RunConversationSnapshot` rows are created in the same transaction as the new `Turn`
and `Run`. Each row records one earlier effective Turn with:

- the source Thread, Turn, author, and selected completed Run;
- the redacted user input and, when available, the selected answer artifact content;
- the source classification, capture time, chronological ordinal, and SHA-256
  fingerprint of the exact stored payload.

The effective history is resolved through the Thread fork lineage. A fork reads its
parent only through `forked_from_turn_id`; later parent Turns cannot enter an existing
branch. Only source Runs completed no later than the new Turn's capture time are
eligible, and the newest eligible completed Run with an answer artifact is selected.
Turns without a completed answer retain their user input and an empty assistant part.

## Bounds and ordering

Capture starts from the newest eligible Turn and is limited by both
`conversation_context_max_turns` and `conversation_context_max_chars`. Each user and
assistant value is independently capped by
`conversation_context_max_chars_per_message`. Selected rows are stored and supplied
to the model in their original chronological order.

The model context builder protects higher-priority platform policy, AgentSpec,
current input, and current Evidence before spending the remaining budget on memory
and conversation history. Previous assistant output is sent as an assistant message.
Previous input from the current principal is sent as user history; content authored
by another workspace member is wrapped as untrusted data.

## Replay and inspection

Run replay clones the recorded conversation rows, remaps their IDs, and includes them
in the replay fingerprint. It never re-resolves current Thread state. The Run
inspection API and Workbench expose the exact captured rows and their fingerprints.
`context.resolved` contains snapshot identifiers and source lineage so event
consumers can correlate the resolved model context without receiving raw content.

## Invariants

- snapshot rows are append-only and reject `UPDATE` and `DELETE` in PostgreSQL;
- `(run_id, ordinal)` and `(run_id, source_turn_id)` are unique;
- ordinals are positive and fingerprints are 64-character SHA-256 hex strings;
- every query includes the organization boundary and Run access check;
- a snapshot never contains the current Turn or a later source Run;
- branch-local capture cannot observe parent Turns after the persisted fork point;
- replayed rows are byte-for-byte equivalent apart from remapped snapshot identity.
