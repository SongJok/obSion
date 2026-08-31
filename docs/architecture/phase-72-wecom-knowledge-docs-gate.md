# Phase 72 WeCom knowledge docs review

## Review question

Can WeCom documents enter Organization Knowledge through Capability Gateway
with pinned egress and explicit or inherited ACL, without Agents receiving
credentials or inventing organization ACL?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Connector `obsion-wecom-docs` calls only `https://qyapi.weixin.qq.com`.
- Credentials come from namespaced environment variables.
- REST `POST /knowledge/sources/wecom/documents` and Capability
  `knowledge.ingest` share the same orchestration.
- WeDrive space sync requires an operator-supplied `space_id` and skips
  unsupported node types or files without a `docid`.
- Missing ACL fails closed.
- The module must not import Harness or IM adapter code.

## Automated acceptance map

- `test_phase72_wecom_knowledge.py` covers id validation, ACL, egress,
  client fetch, executor ingest, REST ingest, and architecture AST.

## Human review checklist

- Confirm operators never commit WeCom secrets.
- Confirm Knowledge remains separate from IM Experience.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
