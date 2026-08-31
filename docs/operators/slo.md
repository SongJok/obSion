# Service level objectives

These are engineering targets for a single control-plane replica under a
governed, read-heavy interactive workload. They are not a signed production SLA.

`GET /api/v1/admin/slo` is the tenant-scoped PostgreSQL projection of success rate,
replan rate, approval rate, satisfaction, evidence coverage, tokens, cost, steps,
and mean wall-clock / model / capability-step latency. Empty denominators are
`null`. TTFT is marked histogram-only (`obsion.run.ttft`). Do not quote that
endpoint as a signed p95 SLA.

| Signal | Target | Source |
| --- | --- | --- |
| `/health/live` and `/health/ready` | HTTP 200 while dependencies are available | FastAPI probes |
| Greeting / CONVERSATION Run | p95 complete under 5s in local pytest concurrency | `obsion.run.duration` |
| Time to first `answer.delta` | Histogram on terminal answers | `obsion.run.ttft` |
| Bounded replans | Counted per missing-evidence or transient failure | `obsion.run.replans` |
| Capability Gateway invocation | Counted and timed per capability and status | `obsion.capability.duration` |
| SQL read path | Timed at the read-only executor | `obsion.sql.duration` |
| Knowledge retrieval | Timed after ACL-filtered ranking | `obsion.retrieval.duration` |
| Policy evaluation | Timed through persist | `obsion.policy.duration` |
| Approval decisions | Counted by capability/action kind | `obsion.approval.decisions` |
| Workflow execution | Counted and timed on terminal automation | `obsion.automation.duration` |
| Run step count | Histogram on completed answers | `obsion.run.steps` |
| Evidence coverage | Histogram of Critic coverage | `obsion.critic.evidence_coverage` |
| SQL | Fail closed on write, UNION to unauthorized tables, stacked statements | AST policy |
| Connector outage | Circuit opens after repeated transport failures | `ConnectorCircuitBreaker` |

Load verification in CI is eight concurrent greeting Runs plus concurrent SSE
streams, not a multi-tenant soak. Operators should replay Knowledge, Data,
Engineering, Incident, and Support questions against staging with their real
connectors before quoting these numbers.
