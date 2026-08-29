# Phase 1 architecture guard review

This review packet supports the required human decision that Obsion has one runtime
protocol and no second message or trajectory model. It records reviewable evidence; it
does not grant approval and it must not be treated as an AI signature.

## Boundary under review

The authoritative runtime hierarchy is `Workspace → Thread → Turn → Run → Step →
Event`. Runtime facts are appended through `EventStore.append`. The `events` table is
the durable source of truth and the registered Event envelope is the only trajectory
contract exposed to runtime consumers.

The following records are deliberately not alternative runtime protocols:

- `outbox_messages` contains the exact validated Event envelope and exists only for
  delivery. It has a unique foreign key to `events` and cannot invent a runtime fact.
- `run_conversation_snapshots` is immutable, bounded model input captured before a
  Run. It cannot establish current facts, advance a Run, or replace Evidence.
- `notification_deliveries` records durable in-app delivery state for governed
  workflows and actions. It is not a conversation or execution trajectory.
- Turn input, Run state, Steps, Artifacts, Claims and Evidence are domain records. Their
  lifecycle changes are emitted as Events; their REST representations are read
  projections rather than another message log.
- JSON-RPC `server.*` and `run.subscription.*` notifications are ephemeral connection
  control frames. Runtime notifications use the registered `event.name` and carry the
  complete `EventView` envelope under `params.event`.

## Automated evidence

`test_single_event_protocol.py` fails when:

1. an unreviewed message, chat, conversation, event, notification, stream or trajectory
   table is added;
2. production code constructs or bulk-writes `Event` or `OutboxMessage` outside
   `EventStore`;
3. raw SQL mutates either Event table;
4. another WebSocket/SSE output boundary appears; or
5. a literal runtime notification bypasses registered Event names.

`test_app_server_api.py` additionally proves that every WebSocket runtime notification
method equals the stored Event name and that its embedded envelope is byte-for-JSON
equivalent to the REST Event projection. `test_event_contracts.py` proves Outbox, REST
and persisted Event values share the same validated envelope and that invalid Events
fail before locks, sequence changes or writes.

Reproduce the review evidence with:

```bash
uv run obsion validate-contracts
uv run pytest \
  services/control-plane/tests/test_single_event_protocol.py \
  services/control-plane/tests/test_event_contracts.py \
  services/control-plane/tests/test_app_server_api.py \
  services/control-plane/tests/test_contract_quality_gates.py
```

## Human review checklist

- [ ] Confirm `events` is the only durable execution trajectory.
- [ ] Confirm all Event/Outbox mutations cross `EventStore.append`.
- [ ] Confirm REST, SSE, WebSocket, SDK and Workbench runtime updates are projections
      of the same Event envelope.
- [ ] Confirm conversation snapshots and notification delivery records cannot advance
      a Run or substantiate a factual Claim.
- [ ] Confirm JSON-RPC control notifications cannot execute lifecycle mutations or
      masquerade as registered runtime Events.
- [ ] Confirm no connector, model provider, Agent or client owns a private trajectory.

Decision: `PENDING`

Reviewer: _unassigned_

Review date: _unassigned_

Notes: _unassigned_
