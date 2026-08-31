# Obsion Engineering Contract

1. The repository is the source of truth.
2. Read this file and `docs/project-status.yaml` before implementation.
3. Inspect existing architecture before modifying it.
4. Preserve backward compatibility unless an ADR explicitly approves a breaking change.
5. Do not implement future phases unless they are required for the current phase contract.
6. Prefer interfaces and contracts before concrete integrations.
7. Never put production credentials or secrets into source code or model context.
8. Production resources are read-only by default.
9. All external capabilities must pass through the Capability Gateway.
10. All authorization decisions must pass through the Policy Engine.
11. All Agent executions must be observable and auditable.
12. Every phase must include implementation, tests, documentation, migration, and validation.
13. Do not mark a phase complete while tests are failing.
14. Fix discovered regressions before completion.
15. Record architectural decisions in `docs/adr`.
16. Update `docs/project-status.yaml` after each phase.
17. Generate `docs/phases/PHASE-XX-REPORT.md` and the matching architecture gate document.
18. Do not fake external integrations. Use explicit mock or development adapters where real credentials do not exist.
19. Never weaken tests to make them pass.
20. Prefer root-cause fixes over local workarounds.

Runtime invariants for this repository:

- One Python control plane. Do not introduce a second backend language.
- PostgreSQL is the transactional source of truth. Kafka and ClickHouse are not the V1 base.
- Workspace → Thread → Turn → Run → Step → Event is the durable Harness model.
- Harness loop: Observe → Understand → Plan → Execute → Verify → Reflect → Respond.
- Agents never receive connector credentials. Policy, not prompt text, decides permission.
