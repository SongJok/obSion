# Phase 5 Workbench shell review

## Review question

The human gate asks whether one identity-gated, three-column Workbench is the correct
user surface for Workspace/Thread navigation, conversation, and transparent Runtime
inspection. Automated completion does not create a human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Product boundary

The Workbench is a client of the existing App Server and management API. It does not
plan, execute tools, call a model, create an alternative trajectory, or infer Run state.
Its only runtime facts are the durable Run, Step, Event, Evidence, Artifact, memory, and
cost projections returned by the control plane.

```text
left                     center                    right
Workspace / Threads  |  Turn / answer          |  Run status
Thread lifecycle     |  one assistant entry    |  Plan / Steps
                     |                          |  latest Event / Cost
```

The shell deliberately does not introduce a SQL editor, dashboard builder, model
selector, Agent selector, or broad new administration workflow during Phase 5.

## Browser authentication boundary

The login page accepts a development bearer or OIDC access token only for a one-time
exchange at `POST /api/v1/auth/session`. The server validates and provisions the
Principal, creates a random opaque session, persists only its SHA-256 digest, and sets
an `HttpOnly`, `SameSite=Strict` cookie. The access token is not written to browser
storage, a URL, or the checked-in UI configuration.

REST and `/api/v1/app-server` resolve the same revocable session and then use the same
Principal authorization path. Explicit bearer authentication remains available for
SDK and non-browser clients. Unsafe cookie-authenticated HTTP requests are checked
against the configured Origin allowlist in addition to SameSite enforcement. Logout
revokes the database row before expiring the cookie.

`auth_sessions` is organization-bound to the provisioned User through a composite
foreign key. Expired, revoked, deactivated, or deleted users cannot regain authority
with a cached cookie. Production requires explicit allowed origins and secure cookies;
development authentication remains prohibited in production.

## Runtime and responsive behavior

- A successful Turn immediately places its Run in the inspector. The Workbench then
  reconciles Run, Steps, Evidence, context, Claims, Artifacts, and cost over REST while
  the App Server streams Event envelopes from the durable Run cursor.
- The right panel renders persisted Steps and status; an empty Run shows an explicit
  pending timeline state instead of a black-box spinner.
- Event delivery remains at least once. The client deduplicates by Event ID, advances
  only the monotonic `run_sequence`, and keeps REST reconciliation as the proxy-safe
  compatibility path.
- On screens at or below 880 px, navigation and Runtime become bounded overlay drawers
  with dismissible scrims. The page shell uses percentage widths, `min-width: 0`, and
  clipped shell overflow; only content that inherently needs it, such as a table or
  code block, may scroll horizontally inside its own panel.
- Authentication expiry unmounts the Workbench and closes active stream/poll work;
  it never turns a failed session into a seeded local-user fallback.

## Automated acceptance map

- `test_phase5_workbench_auth.py` proves opaque cookie exchange, HttpOnly/SameSite
  attributes, REST and WebSocket session sharing, Origin rejection, server-side
  revocation, invalid-token rejection, and a real cookie-authenticated Turn whose
  persisted Plan, Steps, Event cursor, and cost fields form the Runtime timeline.
- `test_phase5_workbench_boundary.py` freezes the one-gate/one-shell/three-column
  composition, Step/status/cost rendering, absence of browser token persistence, and
  mobile no-page-overflow rules.
- `test_postgres_phase5_auth_sessions.py` proves the persisted value is a one-way
  digest, tenant-bound, resolvable before revocation, and unusable after revocation in
  a real PostgreSQL transaction.
- Web lint, strict TypeScript checking, and the optimized Next.js production build
  must pass. Real-browser acceptance on the production build proved the dedicated
  login boundary, a cookie-authenticated Turn with three persisted Steps,
  `run.completed`, and visible cost. At 1440x900 the rendered left/center/right
  widths were 252/836/352 px without page overflow. At 390x844 both document and
  body had `scrollWidth == clientWidth == 390`; the navigation and Runtime drawers
  opened and closed through their scrims, and the browser console had no warning or
  error. Server-side sign-out/revocation remains covered by the auth contract tests.
- Phase 1–4 Event/error/OpenAPI, tenant, App Server boundary, state-machine,
  cancellation, reconnect, SDK, migration, Compose, and Helm gates remain mandatory.

## Human review checklist

- Confirm the shell exposes one Obsion rather than selectable specialist Agents.
- Confirm the left/center/right information hierarchy and mobile drawers.
- Confirm access-token exchange and revocable-session operational policy.
- Confirm Runtime Step and cost labels are understandable to target users.
- Record approver identity, decision, and date only through the real review process.
