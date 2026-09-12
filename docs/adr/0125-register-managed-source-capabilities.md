# ADR 0125 — Register managed source capabilities for ordinary setup

- Status: accepted for development.
- Date: 2026-09-12
- Builds on: ADR 0114–0117 and 0122–0124.

The managed document reader and worker were validated using explicitly created capability records. Inspection of the ordinary development environment found that its normal registry bootstrap did not create these descriptors. An administrator could not bind the worker through the supported management API without first inserting capability definitions directly. This was an integration gap between the validated worker and everyday setup.

The builtin registry now registers `knowledge.dingtalk.discover` and `knowledge.dingtalk.read` with the existing strict adapter input schemas: SDK transport, L2, read-only side effect, `knowledge.write` permission and RESTRICTED data classification. Each has a 120-second capability deadline matching the bounded host-reader workflow. Bootstrap creates neither a connector nor a capability binding, identity binding, source or allowing Policy for these operations. The normal API still does not install the host DWS executor. An operator explicitly configures the verified organization and user, binds the descriptors using the existing administrator endpoints, and starts the development host worker. All actual access remains through Gateway and Policy.

Capability seeds accept optional timeout and classification overrides. Their fingerprint includes those values only when explicitly overridden, preserving every existing default descriptor fingerprint and version. Repeated bootstrap reuses the managed versions. No database migration is added; normal source deployment still requires the existing migrations through `a3b5c7d9e1f2`.

Worker and reader tests now use these real builtin descriptors instead of inserting synthetic definitions and versions. Transport responses remain explicitly synthetic in local tests. A failure-first test reproduced the missing registry entries. Further tests verify no implicit binding, idempotent bootstrap, and setup through administrator HTTP endpoints: installation, user binding, connector, capability bindings, Policy, source registration and pause. Both registration tests also pass with disposable PostgreSQL databases.

The integrated Python regression including ADR 0124/0125 passed 2548 tests, with 252 explicit skips and 7 live deselections, in 633.88 seconds. This supersedes the earlier snapshot for local Python evidence; it does not prove live delivery or production readiness. The source-management increment remains part of the existing productization contract, not a new completed phase.


Ordinary development deployment was subsequently verified using a restored copy of its real database backup before migration, the existing persistent volume, and matching packaged source files. Three real Pointbot messages now cover grounded knowledge, explicit insufficient evidence and General translation. Their actual chat bodies match the protected artifacts. The host worker runs under a local user supervisor and retains document identifiers across restart. Full format coverage and the observed upstream delivery delays remain open; see the [deployment and message ledger](../release/evidence/productization/20260912-normal-managed-source-qa.json).
