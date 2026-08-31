# Phase 22 observability completeness review

## Review question

Can governed read-only Trace, Config, and Kubernetes status queries use the same
Capability Gateway contracts as metrics, logs, Git, and deployments, without opening
a write, restart, or dashboard path?

**Status: PENDING — automated checks do not constitute observability-platform or
security approval.**

## Delivery contract

- `trace.search` and `trace.timeline` join `observability.v1`. Provider spans/traces
  reduce to the existing ObservabilityEvent envelope. Extra span fields stay inside
  the allowlisted `attributes` map.
- `config.get`, `config.diff`, and `k8s.status` join `engineering.v1`. Responses use
  the ChangeEvent envelope with cluster/workload/config attributes. Secrets in
  previous/current values are redacted.
- Observability and engineering connectors always enter their bounded invokers.
  Unknown operations such as `k8s.restart` fail closed before any HTTP request.
- Incident plans may select `trace.search`, `config.diff`, and `k8s.status` when those
  capabilities are registered. Production mutation remains out of contract.

## Automated acceptance map

- `test_phase22_observability_completeness.py` covers span normalization, config
  redaction, Kubernetes status fields, incident planning, HTTP trace search, and
  write-shaped operation rejection.
- Existing Phase 17/18 executor, registry, Gateway, and Evidence gates remain required.

## Human review checklist

- Confirm trace, config, and Kubernetes provider bindings, egress, credentials, and
  service/cluster allowlists.
- Confirm span and config field mappings, identifier hashing, and retention.
- Confirm restart, scale, config write, and dashboard operations remain unreachable.
