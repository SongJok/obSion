# ADR 0113: Organization-bound DingTalk Wiki v2 discovery

Status: Accepted for the repository discovery slice; organization document ingestion remains in progress.
Date: 2026-09-11

The user requires automatic acquisition of authorized organization documents. Live inspection found
that the old `/v1.0/doc/workspaces` integration lacks the mandatory operator unionId, uses obsolete
interfaces and does not traverse directories. DingTalk explicitly says old and current workspace
IDs cannot be mixed. Operator DWS visibility does not establish application authorization.

The additive `dingtalk.wiki.v2` protocol uses the existing Python HttpJsonExecutor, CapabilityGateway,
Policy Engine, CredentialBroker, rate limits and audit. It requires explicit `corp_id` and
`operator_id` connector configuration. App credentials remain environment references. Existing
`dingtalk.docs.v1` connectors and history remain compatible; no existing connector is migrated implicitly.

The client lists `/v2.0/wiki/workspaces` with the documented maximum page size 30 and the operator's
permission role. It includes only TEAM workspaces whose returned corpId matches the configured
organization, excludes PERSONAL/foreign spaces, and derives rootNodeId from the returned workspace.
Every node traversal refreshes this scope before sending `/v2.0/wiki/nodes` requests. Caller-supplied
IDs cannot establish the organization. Each node must match its workspace and have a recognized role.

All pages and child nodes share existing page/node/depth budgets. Empty pages retain continuation;
missing collections, malformed pagination, repeated cursors and node cycles fail closed. A failed or
budget-exhausted traversal never returns a successful partial collection. Node extensions distinguish
adoc, able and other files; a folder has no document ID.

This increment is discovery only. Modern node IDs must not be sent to legacy content endpoints.
Until modern content extraction and source-principal ACL mapping are implemented, both single ingest
and workspace sync explicitly reject the modern protocol. No synthetic successful imports are returned.
A role that permits listing is not a grant to publish the document to every local user.

No migration or public schema change is required for this slice. Follow-up work includes current
content/permission protocols, durable sync progress, resume/refresh/revocation, organization drive and
file-type coverage, followed by actual retrieval and grounded answer acceptance. The goal remains active.

Primary contracts read on 2026-09-11:

- [Current workspace list](https://open.dingtalk.com/document/development/get-knowledge-base-list.md)
- [Current node list](https://open.dingtalk.com/document/development/get-node-list.md)
- [Legacy workspace compatibility notice](https://open.dingtalk.com/document/development/querying-the-list-of-user-team-spaces.md)
