"""Bounded critic-driven evidence gap filling.

After the first capability wave, Critic may report missing required Evidence
types.  Harness may append unused, Agent-authorized, read-only capabilities to
collect those types.  The selector never retries a capability that already ran
and never introduces a write.  The caller bounds the number of critic replan
waves so a persistent gap cannot recurse.
"""

from typing import Any

EVIDENCE_GAP_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "DOCUMENT": ("knowledge.search", "ticket.search"),
    "DATA": ("data.query",),
    "SQL": ("data.query",),
    "METRIC": ("metric.query", "metric.compare", "metric.anomaly", "metric.dimension"),
    "LOG": ("log.search", "log.aggregate"),
    "TRACE": ("trace.search", "trace.timeline"),
    "DEPLOYMENT": ("deployment.list", "deployment.commit"),
    "CONFIG": ("config.diff", "config.get"),
    "CODE": ("code.symbol", "code.search"),
    "GIT": ("git.diff", "git.history", "git.commit"),
}

_CODE_GRAPH = frozenset({"code.symbol", "code.reference", "code.callers", "code.callees"})


def select_gap_capabilities(
    missing_types: tuple[str, ...],
    *,
    available: frozenset[str],
    attempted: frozenset[str],
) -> list[tuple[str, str]]:
    """Return at most one unused authorized capability per missing Evidence type."""
    selected: list[tuple[str, str]] = []
    used: set[str] = set()
    for missing in missing_types:
        for capability in EVIDENCE_GAP_CAPABILITIES.get(missing, ()):
            if capability in available and capability not in attempted and capability not in used:
                selected.append((missing, capability))
                used.add(capability)
                break
    return selected


def gap_step_contract(
    capability: str,
    missing_type: str,
    *,
    question: str,
    template: dict[str, Any],
) -> dict[str, Any]:
    """Build a Gateway-ready capability step from an existing plan template."""
    base_payload = dict(template.get("payload") or {}) if isinstance(template, dict) else {}
    resource = dict(template.get("resource") or {}) if isinstance(template, dict) else {}
    environment = (
        str(template.get("environment") or "production")
        if isinstance(template, dict)
        else "production"
    )
    base_payload["operation"] = capability
    if not isinstance(base_payload.get("query"), str) or not str(base_payload["query"]).strip():
        base_payload["query"] = question
    if capability in _CODE_GRAPH:
        environment = "development"
        resource = {"index": "organization", "scope": "authorized-code-graph"}
    elif capability == "code.search":
        environment = "production"
        if not isinstance(base_payload.get("repository"), str):
            base_payload["repository"] = str(resource.get("repository") or "*")
        resource = {"scope": "authorized-repositories"}
    elif capability.startswith("git."):
        repository = base_payload.get("repository") or resource.get("repository") or "*"
        base_payload["repository"] = str(repository)
        resource = {
            "environment": environment,
            "evidence_type": "GIT",
            "repository": str(repository),
        }
    elif capability == "ticket.search":
        environment = "development"
        resource = {"index": "organization", "source": "ticket"}
        base_payload["operation"] = "ticket.search"
        if "limit" not in base_payload:
            base_payload["limit"] = 8
    else:
        resource = {**resource, "environment": environment, "evidence_type": missing_type}
    return {
        "capability": capability,
        "payload": base_payload,
        "resource": resource,
        "environment": environment,
    }


def gap_step_name(capability: str, missing_type: str) -> str:
    return f"Collect missing {missing_type} evidence via {capability}"
