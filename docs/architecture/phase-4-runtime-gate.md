# Phase 4 runtime protocol review

## Review question

The human gate asks whether the Run state graph, cancellation linearization point, and
durable cursor behavior are suitable as the compatibility baseline for workers and
all streaming clients. Automated completion does not create a human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Frozen state graph

```text
PENDING -> RUNNING | FAILED | CANCELLED
RUNNING -> WAITING_APPROVAL | WAITING_USER | REPLANNING |
           COMPLETED | FAILED | CANCELLED
WAITING_APPROVAL -> RUNNING | FAILED | CANCELLED
WAITING_USER     -> RUNNING | FAILED | CANCELLED
REPLANNING       -> RUNNING | FAILED | CANCELLED
COMPLETED / FAILED / CANCELLED -> no transition
```

Turn creation persists a PENDING Run. Only the shared RunWorker/Harness can claim and
execute it. Model calls remain an internal Harness step through Model Gateway and do
not replace lifecycle state, Steps, Events, Evidence, or terminal verification.

## Frozen streaming contract

- Run streams are ordered by immutable `run_sequence`, not aggregate-local sequence.
- JSON-RPC reconnect supplies `after_sequence`; SSE reconnect supplies `after` and/or
  `Last-Event-ID`.
- All projections use the same persisted Event envelope. Domain event names including
  `answer.delta`, `tool.started`, and `tool.completed` are never transport-private
  messages.
- Delivery is at least once. A consumer processes, deduplicates by Event ID, then
  persists its cursor.

## Frozen cancellation contract

Cancellation commits the request fact and terminal Run state together, clears the
lease, cancels all active Steps, emits ordered cancellation Events, and writes audit.
Scheduler and completion paths serialize Run before Step. Calls already in an external
boundary may finish cooperatively and retain honest cost accounting, but cancellation
prevents their result from reopening the Run or starting another Step.

The acceptance suite proves the dependent-Step barrier with a blocked live Harness
execution and separately verifies atomic Run/Step/Event/audit convergence inside a
real PostgreSQL transaction.

## Human review checklist

- Confirm the allowed state edges and terminal-state immutability.
- Confirm immediate terminal cancellation and the in-flight-call caveat.
- Confirm Run cursor semantics and at-least-once consumer responsibility.
- Confirm breaking changes require a new public protocol version.
- Record approver identity, decision, and date only through the real review process.
