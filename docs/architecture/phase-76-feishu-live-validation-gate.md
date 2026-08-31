# Phase 76 Feishu live-validation review

## Review question

Can an operator validate real Feishu authentication and safe read/denial behavior
without sending a message, ingesting a document, exposing credentials, widening
egress, or treating a smoke test as Capability/ACL acceptance?

**Status: PENDING — live authentication is evidence from one tenant and does not
constitute staging, security, data-owner, or production approval.**

## Delivery contract

- `make validate-feishu-live` requires explicit live opt-in and two environment
  credential names; missing inputs fail before network access.
- Exactly three `@pytest.mark.live` probes run; default CI continues to deselect them.
- The probes authenticate, check a fixed nonexistent document, and list/deny wiki
  spaces. They never call message send or Knowledge ingest.
- Feishu structured HTTP 400 errors are parsed after size bounds and before generic
  status handling.
- Business codes `99992402` and `99991672` map to denied without exposing existence
  or response text.
- Unknown business/transport failures remain failures. Redirects and non-Feishu
  origins remain forbidden.
- No credential-file loader, second Harness, new model path, API, Event, or database
  migration is introduced.

## Automated acceptance map

- `test_phase64_feishu_knowledge.py` covers HTTP 400 missing/scope denial mapping and
  secret/token non-disclosure.
- `test_phase76_feishu_live_validation.py` covers Make opt-in, the bounded marker set,
  and architecture boundaries.
- `make validate-feishu-live` passed all three probes against the available tenant.
- Default `make check` continues to run live tests as explicit skips.

## Human review checklist

- Keep credential injection in an external secret manager with shell tracing off.
- Validate tenant-approved docx/wiki/drive scopes and a real test document separately.
- Exercise allowed and denied Principals through the control-plane Knowledge path and
  inspect Evidence/provenance/Audit before tenant rollout.
- Staging, public callback TLS/DNS, message delivery, and security/data-owner sign-off
  remain separate gates.
