# Contributing to Obsion

Thank you for helping build a governed enterprise agent platform. Contributions are
accepted under the Apache License 2.0 and the Developer Certificate of Origin 1.1; add
a `Signed-off-by` line to commits with `git commit -s`.

## Development setup

Required tools are Python 3.12, uv, Node.js 22, npm, Docker, and Docker Compose.

```bash
cp .env.example .env
make bootstrap
make compose-up
make migrate
make dev-api
```

In another terminal, run `make dev-web`. The Workbench is then available at
<http://localhost:3000> and the development API documentation at
<http://localhost:8080/api/docs>.

## Contribution workflow

1. Open or reference an issue for material changes.
2. Add an ADR for changes to public contracts, persistence, policy, or architecture.
3. Include migrations for schema changes and keep downgrade paths safe.
4. Add tests for behavior, tenant isolation, policy decisions, and failure paths.
5. Run `make check` and `make migration-check` before submitting.
6. Explain user-visible behavior, rollout implications, and security considerations in
   the pull request.

Agents and skills never call connectors directly. All external access must pass through
the Capability Gateway, and every invocation must produce a policy decision and audit
record. New mutating or L3-L5 capabilities are outside the V1 safety boundary.

## Style and compatibility

Python is formatted and linted with Ruff and checked with strict mypy. TypeScript is
checked with ESLint and TypeScript. Public API changes require a compatibility note;
breaking changes are reserved for major releases. Do not commit generated caches,
credentials, production data, or build outputs.
