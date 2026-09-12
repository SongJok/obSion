# ADR 0128 — Governed multi-round knowledge investigation

- Status: accepted for development; integrated regression passed and ordinary development deployment verified.
- Date: 2026-09-12.
- Builds on ADR 0112 and 0118–0127. Phase 98 productization remains open.

The user requires answer quality before speed and repeated, autonomous analysis and
source/tool use. A retrieved excerpt alone does not establish that a question has
been answered. A well-formed abstention or a fully validated but rejecting factual
review may now trigger a bounded investigation, followed by another author and the
same full publication review. Invalid model output, unavailable models and revoked
source access do not authorize speculative retries or publication.

The model proposes one strict JSON SEARCH, READ or STOP action. Local validation
accepts only the run's allowed native `knowledge.search` and `document.read`
capabilities. Document references are local opaque mappings to documents and exact
versions already returned by this run's authorized native reads. Page offsets are
derived locally from durable prior steps, not invented by the model. The model
cannot add a URL, connector, credential, write action or arbitrary MCP operation.
Every accepted proposal becomes a durable Capability step before Verify/Reflect/
Respond and executes through the existing Gateway and Policy Engine. The native
reader checks organization, document and chunk grants, current version and managed
source access and returns ordered bounded pages with provenance. A page means the
caller's authorized chunks, not proof that the entire document has been read.

The default investigation limit is three, configurable from zero to six, within
existing step, token, cost and deadline limits. No parallel backend or separate
execution ledger is introduced. Proposal request/response hashes and validated
review summaries are recorded; internal candidate prose is not copied to plan
metadata. A rejected identical candidate over the same or a subset of linked body
hashes cannot obtain another review just by receiving new Evidence identifiers.
This is exact normalized-text deduplication, not semantic paraphrase detection.
Committed replans resume through the existing Harness and do not repeat completed
capabilities. Managed source access is rechecked after planning and at ordinary publication gates.

The investigation model call inherits the maximum classification across evidence,
workspace, conversation and memory snapshots, as does the author. Native connector
results now carry their actual classification to Evidence and audit metadata;
Gateway takes the maximum of descriptor floor and actual result. Legacy adapters
that omit the additive result field keep their descriptor floor. Native knowledge
search and document read use explicit INTERNAL descriptor floors; actual restricted
records remain RESTRICTED. Existing immutable descriptors are retained and bootstrap
registers new versions. Group delivery still checks each recipient and the group's
classification ceiling; the regression fixture explicitly authorizes a RESTRICTED
audience when testing full-text delivery, and tests an INTERNAL ceiling separately.

Live validation exposed two additional quality defects. Explicit "依据《…手册》…
是什么" questions could enter GENERAL due to the generic "是什么" matcher. Governed
source references now take precedence without disabling creative writing and
translation. Also, a configured profile with no eligible endpoint could fall back
to a raw retrieval excerpt labeled verified. KNOWLEDGE/SUPPORT now return the local
MODEL_UNAVAILABLE refusal for both no eligible endpoint and no model profile. A
positive citation test now explicitly provides a synthetic author and independent
reviewer; it no longer depends on that unsafe fallback.

`plan.updated` v2 adds `knowledge_investigation` and the existing critic-replan
reason. v1 remains unchanged. No database migration is needed; durable JSON plans
and existing Steps support this addition. Migration head remains `a3b5c7d9e1f2`.

The original live endpoint configuration permitted only PUBLIC/INTERNAL. The user explicitly approved CONFIDENTIAL/RESTRICTED processing by the two existing
Kimi endpoints during this validation. A dedicated processing-scope PATCH preserves
all other endpoint configuration and credentials. It requires models.write plus an
ALLOW Policy decision, scopes lookup to the current organization, and records before/
after classes and the decision in audit. DENY/ASK/MASK are rejected with durable
denial audit. The actual grant is limited to the current operator, the two endpoint
IDs and this operation; other endpoint permissions are unchanged. The existing deployment switch forcing
sensitive input to a private-only profile is set false locally under this explicit
authorization; its repository default remains true. The external endpoints are
never mislabeled as private. The two-model/host allowlists and per-endpoint
classification eligibility remain enforced. The system must
not relabel real enterprise documents or bypass endpoint eligibility. Discovery
of the old classification loss prompted pausing the ordinary managed source via its
administrator control API. After the fixed image and pinned worker were deployed, the source was resumed with six available documents and preserved document identifiers. Pure text everyday Q&A remains available. Earlier real
answer checks are content-quality evidence, not evidence that the newly discovered
classification boundary was enforced. A real K3 run on explicitly synthetic INTERNAL material first declined, proposed
a native READ, generated from the new page and passed independent exact-quote
review (four actual calls, 82.36 seconds). This proves a bounded investigation on
a controlled fixture, not general enterprise coverage. Full MCP investigation, all document formats,
and autonomous project delivery are not accepted by this increment.

See the [validation report](../phases/productization-investigation-validation.md)
and [architecture gate](../architecture/productization-investigation-gate.md).

Remaining authorization scope: the new native read checks current ACL/version at
execution for every indexed document, but `KnowledgePublicationGuard` currently
revalidates managed DingTalk provenance at publication/history checkpoints. Extending
those later checks to other imported/native document sources requires additional
implementation and concurrency validation; this increment does not assert that
all source types have the managed source's revocation guarantees.

The offline backup drill previously depended on the removed no-model retrieval
fallback to populate Claim rows. Its isolated TEST app now installs an explicitly
named offline model fixture, scoped to the fixed drill question and synthetic
`dr-drill` document. Normal Harness/Policy/exact-quote verification still runs;
Run.plan records `offline_restore_dataset` and `real_model_calls: false`, and no
ModelCall or remote endpoint invocation is fabricated. The fixture rejects ordinary
development/production settings and is never installed by normal application startup.
Actual PostgreSQL seeding, current migrations and the original row thresholds pass.
This is backup dataset coverage, not model or answer-quality acceptance.
