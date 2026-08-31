# Phase 26 Experience CLI review

## Review question

Can employees and engineers drive Workspace → Thread → Turn → Run through a
repository CLI that uses the existing App Server and REST surfaces, without a second
Harness, without storing credentials in config files, and without weakening Policy,
Evidence, or production read-only defaults?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `obsion-cli` lives in `apps/cli` and depends on `obsion-sdk` only.
- Default protocol is App Server JSON-RPC for Thread/Turn/Run/Approval mutations.
- Workspace create, Evidence, Claims, Steps, and Artifact bodies stay on REST.
- `ask` waits on the durable Run Event stream and prints timeline, answer, Claims,
  and Evidence. Tokens never appear in rendered output.
- Config TOML cannot contain `token`, `password`, `secret`, `api_key`, or `bearer`.
- Architecture tests forbid control-plane, Harness, Model Gateway, and SQLAlchemy
  imports from `apps/cli/src`.
- Python and TypeScript SDKs expose the remaining App Server methods and the generic
  `/api/v1/approvals` decision surface.

## Automated acceptance map

- `apps/cli/tests/test_cli_architecture.py` forbids a second runtime.
- `apps/cli/tests/test_cli_config.py` covers env/flag precedence and credential
  rejection.
- `apps/cli/tests/test_cli_runtime.py` covers App Server thread/turn mutations and
  REST inspection.
- `services/control-plane/tests/test_phase26_experience_cli.py` completes a greeting
  Run through the CLI runtime against the in-process control plane.
- SDK tests cover App Server method wrappers and REST approval decide.

## Human review checklist

- Confirm operator token distribution for `OBSION_TOKEN`.
- Confirm WebSocket ingress to `/api/v1/app-server` in staging before requiring
  `--protocol app-server` as the only supported mode.
