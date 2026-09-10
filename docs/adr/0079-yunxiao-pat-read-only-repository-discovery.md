# ADR 0079: Yunxiao PAT repository discovery stays a Gateway capability

- Status: Accepted
- Date: 2026-09-08

## Context

The official Alibaba Cloud Yunxiao MCP service accepts a personal access token
(PAT) and exposes a broad tool catalog that includes repository, merge request,
pipeline, and other write operations. The repository contract permits MCP only as
an in-process adapter; remote MCP URLs, stdio, `npx`, Docker, and process spawn
remain fail-closed. Giving the remote server's broad tool catalog to an Agent would
also bypass the Obsion Capability Gateway's fixed policy, grant, audit, evidence,
rate, and egress boundary.

The official MCP implementation calls Yunxiao Codeup REST with the PAT in the
`X-Yunxiao-Token` header. Its central endpoint can enumerate organizations visible
to the PAT before listing each organization's repositories.

## Decision

Obsion implements a first-party `yunxiao.devops.v1` HTTP connector with exactly
one L1, no-side-effect capability: `yunxiao.repositories.list`.

- The Gateway resolves the opaque PAT only after Policy, connector grant, schema,
  approval, and rate-limit checks. The adapter sends it only in
  `X-Yunxiao-Token`; it never appears in a URL, capability payload, result,
  Evidence resource, audit metadata, log message, or Agent context.
- A central connector pins `https://openapi-rdc.aliyuncs.com`, first reads the
  PAT-visible organization list, then calls the fixed Codeup repository path for
  every returned organization. A regional connector needs explicit
  `organization_ids` because its organization discovery path is not central.
- The endpoint must be an exact HTTPS origin present in `allowedEgress`.
  Connector configuration cannot override request paths or contain credential-like
  fields. Pagination, organization count, response bytes, output fields, and
  result rows are bounded.
- The connector is not seeded or auto-bound. An administrator creates it as
  `DRAFT`, supplies a secret-manager `credentialRef`, reviews Policy and egress,
  explicitly activates it, and creates the binding.
- Creating repositories, branches, merge requests, pipeline runs, deployments,
  configuration changes, or any other Yunxiao mutation remains outside this
  capability and remains unavailable through this connector.

## Consequences

- The PAT's visible repositories can be discovered without a repository allowlist,
  while Obsion still enforces organization, Principal, Policy, connector grant,
  audit, and evidence boundaries.
- A remote Yunxiao MCP server is not installed and cannot gain a bypass around the
  control plane.
- No database migration, default live connector, external write, production
  promotion, or real-tenant validation claim is introduced.
