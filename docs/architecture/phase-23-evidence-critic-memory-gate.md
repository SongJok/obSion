# Phase 23 evidence / critic / memory review

## Review question

When required Evidence is missing after the first capability wave, can Harness
collect additional authorized read-only Evidence without unbounded recursion, model
self-approval, or a write path?

**Status: PENDING — automated checks do not constitute production or security
approval.**

## Delivery contract

- Critic missing-type detection is deterministic and independent of model output.
- Replanning appends unused Agent-authorized capabilities only. Attempted
  capabilities are never retried by the critic path.
- At most `run_max_critic_replans` (default 1) critic waves per Run.
- GIT Evidence is produced by git.* capabilities. CODE remains the Code Graph
  contract. DATA/SQL are aliases for coverage.
- Memory inspect/edit/delete goes through Policy. Edit of an approved item returns it
  to CANDIDATE. Delete is a durable REVOKED status, not a silent row drop.

## Automated acceptance map

- `test_phase23_evidence_critic_memory.py` covers gap selection, DATA/SQL aliasing,
  GIT as a cause artifact, bounded step insertion before VERIFY, and Engineering
  artifacts.
- `test_api_e2e.py` covers memory inspect, redacted edit, revoke, and double-delete
  conflict.
- Existing Critic, replanning, Evidence Fabric, and Memory policy tests remain
  required.

## Human review checklist

- Confirm critic-replan capabilities cannot bind write connectors in tenant
  registries.
- Confirm GIT redaction of patches and commit messages still holds after the type
  split.
- Confirm revoked memory cannot re-enter Run context snapshots.
