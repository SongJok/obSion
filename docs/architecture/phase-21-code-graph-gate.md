# Phase 21 Enterprise Code Graph Intelligence review

## Review question

Can EngineeringAgent answer authorized symbol, reference, and call-chain questions
from an immutable static Code Graph, with ACL applied before ranking, CODE Evidence
citations, and an explicit unknown answer when the current principal cannot recall
matching source?

**Status: PENDING — automated checks do not constitute source-control, security, or
architecture approval.**

## Delivery contract

- Source is ingested through `POST /code/repositories` and immutable snapshots.
  Parsers never execute repository files. Python uses `ast`; Java and TypeScript use
  conservative scans. Hidden-path escape and oversized files fail closed.
- Repository ACLs require an explicit grant document. DENY wins. Classification
  permissions cannot cross a tenant boundary. Unauthorized repositories have zero recall.
- Identical content checksums reuse the current snapshot. Content changes create a new
  ordinal. `code.symbol`, `code.reference`, `code.callers`, and `code.callees` are
  INTERNAL capabilities bound to `obsion-code-index` through the Capability Gateway.
- Engineering plans resolve `engineering-agent` and `code-architecture`. Answers cite
  repository, path, symbol, and commit. Missing authorized CODE Evidence yields unknown.

## Automated acceptance map

- `test_phase21_code_graph.py` covers AST API/call/SQL extraction, ACL zero recall,
  snapshot idempotence, Engineering routing/planning, and Workbench-level Evidence.
- Registry, Policy, Gateway, OpenAPI, both SDKs, Workbench Code Graph view, static
  error contracts, and existing PostgreSQL/Compose/Helm gates remain required.

## Human review checklist

- Confirm repository ACL sources, deny semantics, and classification mapping.
- Confirm parser coverage and that untrusted source cannot execute in the runtime.
- Confirm Code Graph Evidence is sufficient for EngineeringAgent, including unknown
  answers when authorized symbols are absent.
