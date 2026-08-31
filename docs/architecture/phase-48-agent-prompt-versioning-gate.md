# Phase 48 Agent/Prompt versioning review

## Review question

Can operators compare Agent, Skill, and Prompt versions, roll back an Agent/Skill
runtime cutover to a previously published snapshot, and evaluate two versions on
Eval—without rewriting production specs, splitting Harness traffic, or pretending
Prompt has an `active_version` pin?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `POST /api/v1/studio/compare` diffs Agent, Skill, or Prompt versions.
  `traffic_split` is always `false`. Secret paths are `[redacted]`.
- Compare of Workflow or of two identical version numbers is `registry_spec_invalid`.
- `POST /api/v1/studio/rollback` promotes a previously published Agent or Skill
  version. It does not delete or rewrite the displaced version.
- Prompt rollback is denied. Prompt templates are not edited in place.
- Evaluate stays on `/api/v1/eval`: two Evaluation Runs on the same snapshot, not a
  third Harness loop. `fixtures.actual` is rejected.
- Studio application code does not import Harness, Capability Gateway, or Model
  Gateway. Conversation still has no Agent picker.

## Automated acceptance map

- `test_phase48_agent_prompt_versioning.py` covers mapping redaction, Agent
  compare/rollback history preservation, Prompt compare, Prompt/Workflow rollback
  denial, and AST bans on traffic split / Prompt `active_version`.
- Python and TypeScript REST clients wrap compare and rollback.
- Workbench Studio lists every version, rolls back, and compares.

## Human review checklist

- Confirm operators do not treat compare as live A/B.
- Confirm Prompt compare is not mistaken for a runtime pin.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
