# ADR 0117 — Governed knowledge source management

- Status: accepted for development productization; ordinary runtime rollout remains pending.
- Date: 2026-09-12
- Builds on: ADR 0115 and ADR 0116.

## Problem and decision

A durable worker needs an operator surface that distinguishes discovering files from making their content usable. Add authenticated control-plane routes at `/api/v1/knowledge/sources/dingtalk/managed` and an expandable source manager in Enterprise Knowledge. Only existing same-organization connectors and current-user DingTalk identity bindings are offered. Multiple connections require an explicit selection. Registration accepts connector and binding IDs, never credentials, document bodies or caller-supplied access proofs.

The Policy Engine authorizes management operations with audit records. Source ownership remains mandatory even for administrators. Mutations run in a savepoint so rejected work rolls back while the denial and Policy association commit. A source-specific decision uses the source's stored corporation; controls use the same canonical operation names as the service. Connection identity is refreshed under the registration lock.

Pause disables retrieval and fences outstanding worker claims. Resume invalidates previous READY authorization before queuing a fresh scan; it cannot renew permission by toggling a flag. Manual rescan preserves currently valid content while scheduling fresh discovery. Source and item inventories are paginated and contain metadata only. Availability uses the same mandatory access predicate as knowledge retrieval, rather than counting every READY row. Completed enumeration with partial or failed files remains attention-worthy.

The page exposes usable, pending, incomplete, failed and awaiting-recheck counts, individual issues, pause/resume and rescan. It refreshes visible summaries periodically; expanded item details are explicitly a viewed snapshot and remain open until manual refresh or another action. A fresh inventory replaces stale results, and failed refresh clears previous source metadata and actions. Loading more uses the returned cursor. No custom organization switch, host login or provider call is executed in the browser.

## Compatibility, migration and rollout

Reuse `e1f3a6b8c5d7`; no new database migration. Existing knowledge forms and legacy connectors remain compatible. Management routes are restricted to TEST/DEVELOPMENT; they do not install the host SDK in the ordinary API or start a worker. Updated OpenAPI describes the closed request and metadata response schemas.

Apply the existing source migration and deploy API/Web together before enabling the explicit host worker. A rollback must retain source-access protections and disable active sources; never restore a code version that exposes managed content without the source predicate. Normal development runtime activation is deferred until final publication and historical source-access revalidation are implemented.

## Validation and limits

See the source-document validation report and `20260912-dingtalk-source-management.json`. Real browser controls resumed a zziv source, the real Gateway worker completed another read-only cycle, and the page showed 3 usable of 12 files. Pausing through the narrow-screen UI changed usable count to zero; all three document reads were denied with a Policy-linked pause audit. Synthetic API tests independently cover ownership, Policy denial, connection changes, pagination and closed inputs.

This slice does not establish live pointbot answers, complete document format coverage, cross-user knowledge sharing or production readiness. Previously published answers and in-flight model output still need a source-access guard before ordinary runtime rollout.
