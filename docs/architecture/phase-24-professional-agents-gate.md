# Phase 24 professional agents, skills, and workflow review

## Review question

Can the control plane internally select Analytics, Operation, and Support
specialists (and the missing named Skills) without a user-facing agent picker,
without write paths, and with the sixth support-diagnosis scenario producing
cited DOCUMENT Evidence?

**Status: PENDING — automated checks do not constitute production or security
approval.**

## Delivery contract

- GeneralAgent remains the only user-facing assistant. SUPPORT / OPERATION /
  ANALYTICS routes resolve tenant-active AgentVersions and pinned SkillVersions.
- SupportAgent is limited to ticket/knowledge/log/trace reads. Ticket search is
  INTERNAL, ACL-filtered, and cannot create, close, or comment on tickets.
- OperationAgent plans only read-only status, configuration, log, and metric
  capabilities. Restart, scale, and configuration writes stay fail-closed.
- AnalyticsAgent is selected for funnel/trend/business-analysis language when a
  governed metric is present. SQL compile and `data.query` remain the execution
  path.
- Named Skills exist as versioned manifests with instructions, capabilities,
  required Evidence, and verification. YAML overrides builtins.

## Automated acceptance map

- `test_phase24_professional_agents.py` covers Skill contracts, Understanding
  routes, router pinning, read-only Support/Operation plans, and support e2e
  citations.
- `evaluations/datasets/v1-routing-and-safety.json` includes SUPPORT, OPERATION,
  and ANALYTICS routing cases.
- Existing Knowledge, Data, Engineering, and Incident route tests remain required.

## Human review checklist

- Confirm support questions cannot reach `action.ticket.*` or SQL from the
  SupportAgent Skill.
- Confirm operation questions cannot bind `k8s.restart` or other write-shaped
  operations in tenant registries.
- Confirm ticket documents ingested into the knowledge index carry the same ACL
  and classification gates as other DOCUMENT Evidence.
