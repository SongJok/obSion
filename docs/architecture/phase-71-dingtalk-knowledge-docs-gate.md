# Phase 71 DingTalk knowledge docs review

## Review question

Can DingTalk documents enter Organization Knowledge through Capability Gateway
with pinned egress and explicit or inherited ACL, without Agents receiving
credentials or inventing organization ACL?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Connector `obsion-dingtalk-docs` calls only `https://api.dingtalk.com`.
- Credentials come from namespaced environment variables.
- REST `POST /knowledge/sources/dingtalk/documents` and Capability
  `knowledge.ingest` share the same orchestration.
- Workspace sync skips unsupported node types.
- Missing ACL fails closed.
- The module must not import Harness or IM adapter code.

## Automated acceptance map

- `test_phase71_dingtalk_knowledge.py` covers id validation, ACL, egress,
  client fetch, executor ingest, REST ingest, and architecture AST.

## Human review checklist

- Confirm operators never commit DingTalk secrets.
- Confirm Knowledge remains separate from IM Experience.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
