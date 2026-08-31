# ADR 0057: Vendor Knowledge browsing uses canonical no-Run Capabilities

- Status: Accepted
- Date: 2026-08-30

## Context

Phase 77 moved Feishu, DingTalk, WeCom, and Confluence ingest/sync writes to the
no-Run Capability Gateway entry. Eight source-browsing GET routes still resolved a
Connector and credential inside the REST module and called vendor clients directly.
They checked the caller's `knowledge.write` permission, but bypassed the versioned
Capability binding, resource selector, PolicyDecision, connector grant, schema,
shared rate key, timeout, masking, telemetry, and capability Audit contract.

The vendors expose different nouns and shapes: Feishu spaces/nodes, DingTalk
workspaces/nodes, WeCom space metadata/files, and Confluence spaces/pages. Encoding
those differences in the REST plane would preserve four execution paths instead of
one Capability Fabric.

## Decision

Define two versioned, vendor-neutral HTTP Capabilities:

- `knowledge.source.containers` for spaces, workspaces, and an explicitly addressed
  WeCom space;
- `knowledge.source.items` for nodes, files, and pages inside one container.

Both contracts are L1, `SideEffect.NONE`, and continue to require
`knowledge.write`. Retaining that permission preserves the existing source-management
authorization boundary and does not grant ordinary Knowledge readers access to
vendor inventories. Their strict schemas normalize vendor values into bounded
container/item envelopes. Each binding selects exactly one source and the HTTP
executor maps the canonical result back to the unchanged REST response view.

The existing `CapabilityGateway.invoke_operator` admission boundary accepts only the
two new L1/no-side-effect contracts in addition to the Phase 77 L2 idempotent writes.
The Gateway persists the selected CapabilityVersion on PolicyDecision and Audit,
enforces Policy/grants/schema/rate/credential/timeout/masking/telemetry, and never
creates a Run, Step, Event, Evidence, Approval, or Agent version. Policy DENY/ASK and
rate denial remain before secret resolution. Connector-configured knowledge budgets
bound paginated browsing.

## Consequences

All vendor Knowledge REST source management now shares one governed Capability
boundary. REST remains a compatibility adapter and no longer imports CredentialBroker
or vendor Connector resolvers. Run-scoped Agent behavior is unchanged; the new browse
Capabilities are not added to AgentSpecs and operator results are not Run Evidence.

No relational, Event, REST path, request, response, or status-code migration is
required. Builtin seeding creates immutable Capability definitions/versions and four
source-specific bindings. A later real Run may create Evidence only when it retrieves
Organization Knowledge through its own authorized capability path.
