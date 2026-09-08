# ADR 0079: Clarification is a bounded, context-first Harness lifecycle

- Status: accepted
- Date: 2026-09-04
- Phase: 100

## Context

Obsion already represented `WAITING_USER` in the durable Run state machine, but the
Harness had no governed way to use it when a request was too ambiguous to plan safely.
The two naive alternatives are both wrong for an enterprise Agent runtime:

- selecting the first catalog or context match silently turns uncertainty into an
  unaudited decision; and
- asking immediately ignores already-authorized conversation, memory, workspace, and
  catalog context, producing avoidable interruptions and potentially soliciting data
  the Agent must never receive.

Clarification also cannot become a second chat protocol. It must remain inside the
durable Workspace → Thread → Turn → Run → Step → Event model, preserve the Run's
execution budget while the user is away, reject stale concurrent answers, and expose
enough information for every client without publishing server-only canonical values or
resolution provenance.

## Decision

1. **Explore before asking.** After Context resolution and before Plan creation, the
   Harness deterministically examines only context already visible to the principal:
   explicit input and context references, prior visible conversation, governed memory,
   Workspace context, and organization-scoped catalogs. Repository candidates remain
   ACL-filtered. One highest-confidence value is resolved automatically; zero values or
   an unresolved tie becomes a clarification gap. The resolver never chooses the first
   catalog row merely because it is first.

2. **Persist a typed Intent, not an ad-hoc prompt transcript.** `Run.intent` keeps the
   versioned `RunIntent` contract, a monotonic `intent_revision`, preparation stage,
   resolved-slot provenance, and clarification lifecycle. Existing Runs without the
   typed schema remain readable when they are not waiting for a user. A `WAITING_USER`
   Run whose typed state is absent or corrupt fails closed instead of guessing how to
   resume it.

3. **Bound the interaction.** A Run may request at most two clarification rounds; each
   request contains at most three fields and each field at most three visible options.
   Fields are single-valued and typed as `STRING` or `TIME_RANGE`. A request must offer
   at least one opaque option or explicitly allow bounded free text. Repeating the same
   unresolved gap does not consume another round under different prose; it is rejected
   by a canonical gap fingerprint.

4. **Never ask for credentials or server decisions.** Slot names are normalized and
   reject credential, token, private-key, DSN, authorization, cookie, private-endpoint,
   and equivalent Unicode-normalized forms. The same boundary rejects server-derived
   fields such as route, risk, Agent, Skill, decision, Intent, and schema version. Policy
   and Capability selection therefore cannot be changed through a clarification answer.

5. **Keep canonical values server-side.** An option has an opaque UUID and a display
   label in public views. Its canonical value, value/source fingerprints, source
   reference, stored answers, remaining execution time, and answering identity remain
   in the internal typed Intent only. `clarification.requested` carries the same positive
   public projection; `clarification.answered` carries only the affected slot names and
   a response fingerprint; `clarification.expired` carries only its identifier. Every
   payload schema is recursively closed with `additionalProperties: false`.

6. **Resume exactly one active revision.** Answering requires Workspace write access,
   locks the Run row, verifies `WAITING_USER`, the active clarification UUID, its expiry,
   and `expected_intent_revision`, and requires exact coverage of the requested fields.
   An option UUID is mapped to its canonical value only inside the control plane. A
   valid answer records immutable answer provenance, increments the Intent revision,
   transitions the same Run back to `RUNNING`, and continues at planning. Duplicate,
   late, stale, partial, unknown-option, and malformed answers fail closed with stable
   conflict or validation errors.

7. **Separate user-wait time from execution time.** Entering `WAITING_USER` snapshots
   the positive remaining execution seconds, clears the active execution deadline and
   lease, and records an indexed clarification expiry. Resumption creates a new
   execution deadline from that preserved remainder; time spent waiting is not charged
   as model/tool execution. The worker expires unanswered requests deterministically,
   closes their typed state, and fails the Run with `clarification_expired`.

8. **Use the one Event and Audit planes.** Request, answer, and expiry are durable
   versioned Events; Run state transitions and terminal failure remain ordinary Run
   Events. User answer and system expiry also write Audit records. REST, App Server,
   Web, and SDK surfaces are projections and commands over this one control-plane
   lifecycle, never independent Agent loops.

## Consequences

- A vague request can be resolved from visible context without interruption, but the
  Harness stops before planning whenever a blocking choice remains genuinely ambiguous.
- A clarification resumes the original Run and Turn; it does not invent a second Run,
  message table, credential path, or authorization decision.
- Stored canonical values remain available for deterministic planning and Replay while
  public Run/Event surfaces cannot disclose them.
- Two-round and schema limits make resource use, UI rendering, replay, and auditing
  finite. Exhaustion or repeated non-progress is an explicit failure, not recursion.
- The extraction/ranking rules are intentionally deterministic. Adding a semantic or
  remote resolver later must still use authorized context and enter through governed
  Capability/Policy contracts; it cannot bypass this lifecycle.

