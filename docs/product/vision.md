# Product vision and scope

## Positioning

Obsion is an Enterprise Agent Runtime and Intelligence Workspace. Its job is not merely to answer questions; it enables governed AI agents to safely and traceably complete knowledge, analytics, engineering, support, and operational work over existing enterprise systems.

The primary user sees one assistant. The runtime selects specialized agents, skills, and capabilities behind that surface.

## User outcomes

### Engineering and incident response

An engineer can ask why a latency or business metric changed after a deployment. Obsion correlates metrics, logs, traces, deployments, configuration, and code changes, then exposes every conclusion with its supporting evidence.

### Operations and analytics

An operator can ask about conversion, funnels, segments, or anomalies. Obsion resolves governed metric definitions, constructs a logical query, validates generated SQL, executes it through a read-only gateway, and returns tables, charts, SQL, and findings.

### Product and knowledge

A product owner can ask how a real business flow works. Obsion combines permission-filtered PRDs, code, APIs, schema, logs, and tickets without leaking documents that the user cannot access.

### Customer support

A support agent can investigate a customer-specific failure through masked, purpose-limited capabilities. The answer provides classification, reason, internal handling guidance, and a customer-safe response without granting raw production access.

### Management

A manager can receive a sourced operational picture that combines business metrics, incidents, deployments, and customer feedback.

## Invariants

1. An agent is a model plus harness, context, capabilities, policy, memory, environment, and feedback loop.
2. Harness contracts do not depend on a model vendor.
3. Agents never connect directly to production resources.
4. MCP is one transport behind the capability boundary, not the architecture.
5. Material factual claims require evidence, source, timestamp, and confidence.
6. Every run is event-sourced and replayable from recorded inputs and outcomes.
7. Authorization is enforced in code at execution time, never by prompt text.
8. Users interact with one assistant; routing is an internal concern.

## Version-one product boundary

Version one establishes the complete platform foundation and drives three scenarios end to end:

- governed enterprise knowledge with inherited ACLs and citations;
- governed enterprise analytics with semantic metrics and read-only SQL;
- incident investigation across metrics, logs, traces, deployment, configuration, and code.

Version one also introduces a closed governed-action path for PR and ticket operations
in development/staging. It requires immutable preflight, independent execution and
rollback approvals, current owner authorization, a real idempotent provider, and full
event/audit evidence. Production remains read-only. Configuration, Kubernetes,
database, restart, deployment, destructive, and production actions remain denied
until separate operational-readiness gates are delivered.

## Explicit non-goals

- unrestricted autonomous production remediation;
- production database writes;
- a marketplace of unreviewed plugins;
- dozens of overlapping user-selected agents;
- forcing every integration through MCP;
- recursive agent execution without bounded budgets;
- treating a generated SQL string or model answer as trusted output.

## Success measures

Security and correctness gates are product requirements, not future hardening:

- all capability requests produce a policy decision and an audit record;
- all production data access is brokered, read-only, bounded, and masked;
- credentials are resolved only inside the execution boundary and never enter model context;
- document retrieval applies source ACLs before ranking and generation;
- every completed investigation can be resumed, forked, inspected, and replayed;
- final claims expose evidence coverage and critic verification results.
