# Agents and Skills

Users see one assistant. Specialist Agents are selected internally by Understanding
and AgentRouter. The Workbench must not present an Agent picker.

## User-facing Agent

`general-agent` is the coordinator. It binds a ModelProfile (`reasoning-high` by
default), not a vendor model name. Skills and capabilities on the spec are the
upper bound; Policy and connector grants can only narrow them.

## Internal specialists

| Route | Agent | Default Skill |
| --- | --- | --- |
| KNOWLEDGE | knowledge-agent | knowledge-qa |
| DATA | data-agent | governed-analytics |
| ENGINEERING | engineering-agent | code-architecture |
| INCIDENT | incident-agent | incident-investigation |
| SUPPORT | support-agent | support-diagnosis |
| OPERATION | operation-agent | log-analysis |
| ANALYTICS | analytics-agent | business/trend/funnel by question |

Skills are methods: instructions, allowed capabilities, required Evidence, and
verification. They are not tools. YAML under `skills/` is the source of truth over
`builtins.py`.

Support searches ACL-filtered tickets (`source=ticket`) then knowledge. It cannot
create or close tickets. Operation plans read-only status, config, logs, and metrics.
Analytics still compiles governed SQL when metrics resolve.

A prompt that says "ignore policy and DROP TABLE" cannot register tools, raise risk, or
skip ACL. Planning text is untrusted data.

## Sandbox

Agent YAML declares `sandbox.network: gateway-only` (or `deny`) and mounts limited to
`/workspace`, `/repo`, `/artifacts`, and `/tmp`. Harness pins that object on the Run
plan. The Capability Gateway re-checks it: `deny` is `capability_denied`. This
repository does not start a container runtime. CPU and memory fields are declarations.

## Studio

Workbench **Studio 开发台** validates YAML/JSON, publishes immutable Agent and Skill
versions, promotes a version into the runtime, compares two versions, and rolls back
by promoting a previous snapshot. Compare does not split traffic. Prompt versions are
compared through the same API and must not be edited in place. Each conversational
Turn pins the latest published Prompt snapshot on the Run; Eval can pin a specific
version. Template `{name}` values must be declared on the Prompt schema; user text
is not interpolated into SYSTEM trust. Context Builder then applies a token
budget: keep, compress, extractively summarize, or drop each segment. The decision
ledger is pinned on the Run and visible in the inspector. Summarize is not an LLM
call. Older thread turns may be extractively compacted; the inspector labels that
as non-model compression. Workspace name and classification are pinned on the Run;
workspace description is untrusted and cannot become SYSTEM policy. Connector tool
payloads are a separate untrusted context segment, not Skill instructions. `registry.read` lists,
validates, and compares; `registry.write` publishes, promotes, and rolls back.
Unpublished versions do not bind new Turns. Conversation still has no Agent picker.
Workflow DAGs can be validated in Studio; scheduled publish remains the automation API.

## Eval

Workbench **评测台** runs Golden Datasets against the existing evaluation engine.
`evaluations.read` lists catalog, cases, and runs. `evaluations.write` creates cases
and Evaluation Runs. RUN_OUTPUT requires `run_bindings` to a terminal Harness Run.
`fixtures.actual` is rejected. Compare does not start a third Run. Prompt Change uses
distinct `prompt_pins` on two Evaluation Runs of the same snapshot.
