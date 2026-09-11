# Yunxiao live validation

## Governed cross-organization inventory

The current management entrypoint is
`POST /api/v1/admin/yunxiao/connectors/{connector_id}/repositories`. It uses the
existing `yunxiao.repositories.list` capability through Policy, Gateway and
CredentialBroker, with audit records and current administrator permission checks
before and after remote I/O. It does not create a Run for a management query.

Create an ACTIVE `yunxiao` connector with protocol `yunxiao.devops.v1`, the exact
Central HTTPS origin, `code.read` grant and an external credential reference.
Bind `yunxiao.repositories.list` to
`{"connector_id":"<actual connector UUID>","source":"yunxiao-catalog"}`.
Omit `organization_ids` to discover organizations visible to the configured PAT;
setting it deliberately restricts discovery to those organizations. Required
current permissions are `admin.read`, `connectors.read` and `code.read`.

Request `{"operation":"yunxiao.repositories.list","page":1,"per_page":100}`,
then follow `next_page`. Preserve the declared total and deduplicate by
`(organization_id,id)` across pages; restart the enumeration when the total or
identities drift. A changing remote catalog is not a transactional snapshot.
`complete=true` means the final page of the declared scope, not verified file
access, all possible scopes, sandbox checkout or production readiness.

The 2026-09-11 local deployment verified 129 repositories in two organizations.
The operator explicitly identified the legacy environment variable
`OBSION_CODEUP_APP_ID` as this installation's PAT source; the connector stores only
its `env://` reference. Do not infer PAT semantics from that variable name for
other deployments. Prefer a clearly named externally injected read-token variable.
See [the validation record](../phases/productization-m1c-validation.md).

## Historical adapter-only smoke test

This is an opt-in, read-only smoke test for an operator-owned Alibaba Cloud
Yunxiao tenant. It calls the first-party `yunxiao.repositories.list` adapter
with a bounded request. It does not create a Connector row, binding, Run,
Evidence item, or vendor resource.

## Safety contract

- The target requires `OBSION_YUNXIAO_LIVE=1` and `OBSION_YUNXIAO_PAT` in the
  current process environment. Inject the PAT from a secret manager with shell
  tracing disabled; never put it in YAML, `.env`, a URL, or a log.
- The default origin is exactly `https://openapi-rdc.aliyuncs.com`. Set
  `OBSION_YUNXIAO_ENDPOINT` only for an operator-approved regional HTTPS origin.
  For a regional probe, set `OBSION_YUNXIAO_ORGANIZATION_IDS` to a comma-separated
  list of operator-approved organization IDs. Regional Connector manifests must
  also declare explicit `organization_ids`.
- The probe requests page 1 with at most 10 repositories, follows only fixed
  organization and Codeup repository paths, and sends the PAT only as
  `X-Yunxiao-Token`. It never sends `Authorization: Bearer`, a request body, or
  a write request.
- Output is limited to pytest status. Vendor response bodies and PAT values are
  not printed. A skipped probe is not validation evidence.

## Run

From the repository root, inject the secret into the process and run:

```bash
OBSION_YUNXIAO_LIVE=1 OBSION_YUNXIAO_PAT='[secret-manager value]' make validate-yunxiao-live
```

For a regional origin, add `OBSION_YUNXIAO_ENDPOINT` and
`OBSION_YUNXIAO_ORGANIZATION_IDS=org-a,org-b` to the same process invocation.

The command fails before pytest if opt-in or the PAT is missing. A successful
run proves that the PAT can enumerate its visible organizations and read a
bounded repository page. It does not prove Policy authorization for an Obsion
Principal, staging deployment health, or production readiness.

## Failure handling

- `401`/`403`: rotate or replace the PAT through the secret manager and verify
  its Yunxiao Codeup read scope. Do not paste vendor responses into an issue.
- Timeout or transport failure: verify DNS, TLS, proxy, and the exact allowlisted
  origin, then retry once. Do not widen egress or switch to remote Yunxiao MCP.
- Malformed or oversized responses are connector failures and must be fixed at
  the integration boundary. Do not weaken normalization or output limits.

After the probe, unset `OBSION_YUNXIAO_LIVE`, `OBSION_YUNXIAO_PAT`, and any
endpoint override. Live results remain operator-owned evidence until recorded
through an approved release process; this test does not update promotion status.
