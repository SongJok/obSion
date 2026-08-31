# Feishu live validation

This is an opt-in, non-sending connector smoke test. It proves that an operator-owned
Feishu application can authenticate and that the document/wiki clients fail closed
with the tenant's real API behavior. It is not a staging deployment, a Capability
Gateway invocation, or permission to bypass Policy and ACL.

## Safety contract

- The target requires `OBSION_FEISHU_LIVE=1`, `OBSION_FEISHU_APP_ID`, and
  `OBSION_FEISHU_APP_SECRET` in the process environment.
- No credential-file loader is provided. Inject values from the deployment secret
  manager and ensure shell tracing is disabled.
- The target never sends an IM message, ingests a real document, creates an Artifact,
  or mutates a vendor resource.
- Output contains test names and safe status only. Tenant tokens, app ids, app secrets,
  vendor response bodies, and document content are not printed.
- Only `https://open.feishu.cn` is contacted and redirects remain disabled.

## Run

From the repository root, inject the two credential values into the current process
and run:

```bash
OBSION_FEISHU_LIVE=1 make validate-feishu-live
```

The Make target rejects missing opt-in or credential environment names before pytest
starts. Exactly three tests carry the `live` marker:

1. Authenticate the Feishu IM delivery client and inspect only bounded token-expiry
   metadata. No chat id or message body is supplied.
2. Authenticate the Knowledge client and request a fixed, nonexistent document token.
   Feishu code `99992402` is normalized to the same denied result as an inaccessible
   document, avoiding an existence oracle.
3. Authenticate the Knowledge client and list wiki spaces. A permitted empty/list
   result or Feishu permission code `99991672` is accepted as a safe read-only outcome.

The application normally needs the tenant-approved read scopes for docx, wiki, and
drive resources. Authentication success alone does not prove those scopes or a
document ACL. For a real tenant rollout, use an operator-supplied permitted test
document through the control-plane Knowledge path, confirm an allowed and denied
Principal, verify Evidence/provenance, and retain Audit. Never invent a document id or
organization-wide ACL merely to make a smoke test pass.

## Failure handling

- Authentication failure: rotate or correct the secret reference and verify the app
  is published. Do not paste the response or credentials into an issue.
- Transport timeout: preserve the safe error, verify proxy/DNS/TLS and exact egress,
  then retry once. Do not widen the origin allowlist.
- HTTP 400 with a documented vendor business code: parse the bounded JSON envelope
  before classification. Missing/inaccessible resources remain denied; unknown codes
  remain connector response failures.
- Wiki permission denied: grant only the tenant-approved read scope and resource
  access. Do not convert it to organization-wide ACL.

Phase 78 also provides a separate no-write Gateway probe. It calls the real Feishu
wiki-space browse through the REST → no-Run Capability Gateway path, accepts either a
bounded list or the normalized scope denial, and verifies Policy/Audit without Run
Evidence:

```bash
OBSION_FEISHU_BROWSE_LIVE=1 make validate-feishu-browse-live
```

This second target requires the same two environment credentials and never ingests,
syncs, sends, or modifies a vendor resource. It is separate from the original three
adapter probes so the Phase 76 bounded smoke contract remains reproducible.

Live validation results are ephemeral release evidence. Normal Agent and operator
Knowledge work still executes through the Control Plane, Capability Gateway, Policy,
connector grants, rate/sync budgets, ACL, Evidence, and Audit.
