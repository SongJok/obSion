# ADR 0075: Post-conclusion context actions

- Status: accepted
- Date: 2026-09-01
- Phase: 96

## Context

The Runtime inspector's claims tab shows Critic-verified conclusions
with their evidence chains, but the investigation loop stopped there:
turning a conclusion into follow-up work meant leaving the inspector,
opening Collaboration, and re-typing the statement — losing the source
Run provenance the collaboration ledger supports since Phase 92. The
gap audit tracked this as "post-conclusion context actions".

## Decisions

1. **Actions on conclusions, not on runs.** The buttons live per-claim
   in the claims tab (转为任务 / 记录决策), because the claim — not the
   run — is the unit an operator acts on. Every created record carries
   `source_run_id`, so provenance flows into the task/decision ledger
   and Phase 92's provenance links render immediately.

2. **Completed runs only.** Actions appear only when the inspected run
   is COMPLETED and pinned to a workspace context; acting on a partial
   investigation would record conclusions the Critic has not finished
   verifying.

3. **Prefilled, editable payloads.** Pure helpers in
   `claim-actions.ts` build the task payload (title, description with
   statement, provenance line, verification status) and decision
   payload (title, bounded summary, rationale with capped evidence
   lines and an explicit remainder note). The modal lets the operator
   edit before saving; nothing is written without confirmation.

4. **Workspace from the run, not the session.** The target workspace
   is `run.workspace_context.workspace_id` — the workspace the run was
   pinned to — so an inspection opened from another surface cannot
   silently file records into whatever workspace happens to be
   selected. The backend's `workspace_source_run_mismatch` validation
   remains the fail-closed enforcement and is mapped to an actionable
   message.

5. **Navigation reuse.** "在协作中查看" closes the inspector and
   switches the Workbench to the collaboration view through a new
   `onOpenCollaboration` prop — no second navigation path.

## Consequences

- The investigation → collaboration loop closes: verified conclusions
  become tasks and decisions with one review step, keeping evidence
  citations and source-Run provenance intact.
- No backend change; the phase composes Phase 92's collaboration
  endpoints and Phase 90's claim surface.
- The candidate contract, recorded evidence, and all six PENDING
  operator gates are untouched.
