# Phase 13 KnowledgeAgent and `knowledge-qa` review

## Review question

The human gate asks whether the first production scenario is complete: KnowledgeAgent
must stay inside the authorized Knowledge surface, every factual answer must carry
inspectable citations, and insufficient evidence must produce an explicit unknown
answer. Automated completion does not create a human scenario or data-owner sign-off.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Route contract

GeneralAgent remains the only user-facing entry point. A `KNOWLEDGE` Understanding route
resolves the organization’s active `knowledge-agent` AgentVersion and `knowledge-qa`
SkillVersion. The Run stores the selected Agent/Skill snapshot and checksum for replay;
the user cannot select or override the specialist.

KnowledgeAgent is restricted to `knowledge.search` and `document.read` at risk level
L1. The Skill explicitly forbids SQL, metrics, logs, traces, code, tickets, and
production-resource access. Event v1 schemas receive only their existing public
Understanding/Plan projection; internal route metadata remains in the Run snapshot.

## Answer contract

Only current-Run, authorized, substantive DOCUMENT Evidence may support a Knowledge
Claim. Each answer has a deterministic citation section with source, document title,
version, chunk, and Evidence ID, and the Claim↔Evidence links remain inspectable through
the existing Run APIs and Workbench.

When search returns no authorized substantive evidence, the system discards any model
claim and answers explicitly `不知道：…`; it creates no Claim or citation and Critic
marks the result unverified. This prevents a fluent but unsupported answer from passing
as Knowledge truth.

## Golden Dataset

`evaluations/datasets/v1-knowledge-qa.json` contains 20 immutable `RUN_OUTPUT` cases:
16 authorized questions and four user/role/department denial cases. Authorized cases
require a DOCUMENT citation, full Evidence coverage, and faithful Claims. Denial cases
require zero recall semantics and the explicit unknown answer.

## Executed gate evidence

- `test_phase13_knowledge_agent.py` passed its route, Skill snapshot, citation, and
  unknown-answer cases; the Phase 13/12/11/9/Critic targeted set passed 21 tests.
- Full Python suite passed: 347 tests, with 18 PostgreSQL-only tests skipped by default.
- Contract, error/event static analysis, Ruff, format, strict mypy, registry, and
  evaluation validation passed (`8` agents, `4` skills, `4` connectors, `27` cases).
- TypeScript SDK passed 14 tests; frontend lint, typecheck, and production build passed;
  Compose rendered successfully; Helm lint/template passed through the pinned Helm
  container.
- A fresh PostgreSQL/pgvector database upgraded through the complete Alembic chain,
  `alembic check` reported no drift, and the integration suite passed 15 tests with
  three destructive migration tests intentionally skipped.

## Human review checklist

- Confirm the KnowledgeAgent question boundary and that no user-facing path can select a
  broader specialist or bypass the Skill capabilities.
- Confirm citation labels and metadata are understandable to users and resolve to the
  safe Evidence/Claim inspection surface.
- Confirm unknown wording is suitable for supported locales and is not interpreted as a
  factual conclusion.
- Review all 20 Golden Dataset questions against real source documents and authoritative
  ACL fixtures, including deny precedence and zero-recall cases.
