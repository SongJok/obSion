# Phase 74 Knowledge citation UI review

## Review question

Can operators inspect Knowledge citation provenance (source, connector,
external id, revision, operation) in the Workbench without the UI inventing
missing fields?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Knowledge search results show provenance from SearchHit fields.
- Runtime Inspector shows provenance for DOCUMENT evidence hits.
- Missing provenance is explicit; values are never invented.
- No second control-plane language; no marketplace.

## Automated acceptance map

- `test_phase74_knowledge_citation_ui.py` asserts Workbench components and
  helpers wire provenance fields.

## Human review checklist

- Confirm UI copy remains accurate for Feishu / DingTalk / WeCom / Confluence.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
