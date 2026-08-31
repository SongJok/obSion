# PHASE-21-REPORT — Enterprise Code Graph Intelligence

## What was implemented

Phase 21 lands a governed static Code Graph on the existing Python control plane.

- Domain tables: `code_repositories`, `code_snapshots`, `code_source_files`,
  `code_symbols`, `code_graph_edges`, `code_repository_grants`.
- Static parsers extract modules, classes, functions/methods, HTTP routes, SQL table
  reads/writes, and call/reference edges. Repository bytes are never executed.
- ACL-before-ranking search powers `code.symbol`, `code.reference`, `code.callers`,
  and `code.callees` through the internal `obsion-code-index` connector.
- Harness Engineering route pins `engineering-agent` + `code-architecture`, plans a
  sequential Code Graph DAG, cites CODE Evidence, and answers unknown without evidence.
- Workbench, Python/TypeScript SDKs, OpenAPI, error catalog, and ADR 0004 were updated.

## Architecture decisions

- The graph is an INTERNAL capability fabric, not an HTTP Git write path.
- Bindings use `environment=development` and `resource_selector.index=organization`,
  matching Knowledge. Engineering plan resources include that selector.
- Graph steps are sequential (symbol → reference → callers) so SQLite tests and
  snapshot writes cannot interleave two INTERNAL sessions on one Run.

## Validation

- `uv run pytest` — 384 passed, 18 opt-in PostgreSQL tests skipped.
- Contract validation: 270 error codes, 92 event versions.
- Evaluation validation: 29 cases (23 RUN_OUTPUT, 4 ROUTING, 2 SQL_POLICY).
- Python/TypeScript SDKs, Workbench typecheck/lint, and Ruff/mypy for the Code Graph surface.

## Remaining risks

- Non-Python parsers are conservative and will miss some call edges.
- Broad production Git, auto-PR, and executing untrusted source remain out of contract.
- Trace/config/Kubernetes completeness remains Phase 22.
