# Phase 12 Knowledge pipeline and ACL RAG review

## Review question

The human gate asks whether Knowledge ingestion and retrieval preserve tenant and
document authorization from source bytes through chunks, embeddings, ranking, and
Evidence. Automated completion does not create an ACL, data-governance, or source-owner
signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Ingestion contract

Supported parsers produce versioned `Document` and `DocumentVersion` rows with source
checksum, parser metadata, extracted text, heading paths, and bounded chunks. A
document is not indexable without an explicit ACL. ACLs are normalized into
organization, user, role, and department allows plus deny labels, and the same
classification and grants are transactionally applied to every current chunk.

Re-ingesting identical content is still an authorization mutation: it keeps the
version identity and rebuilds current chunk grants when the ACL or classification
changes. Embeddings use the Model Gateway and are stored with the chunk in
PostgreSQL/pgvector; a failed embedding operation cannot leave a partial searchable
index.

## Retrieval contract

Every search is scoped by organization, current non-deleted document version, and
chunk grants before candidate ranking. Explicit denies win over direct, role,
department, or organization allows. Classification permissions never elevate a
principal across a tenant boundary. PostgreSQL combines ACL-filtered lexical/vector
candidates with deterministic reranking; SQLite is a test-only lexical equivalent.

The public `SearchHit` projection contains only safe document, version, chunk,
heading, source, content, score, and classification fields. Knowledge results enter
the shared EvidenceFabric as `DOCUMENT` Evidence before Claims or the UI can consume
them. Document detail and download paths reuse the same ACL decision, and an
unauthorized document has zero recall rather than a filtered-after-ranking leak.

## Automated acceptance map

- `test_phase12_knowledge_pipeline.py` covers identical-content ACL tightening,
  current chunk grant rebuild, and zero recall for a denied principal.
- Knowledge service and API end-to-end tests cover parser/version/checksum metadata,
  heading-aware bounded chunks, ACL filtering, embedding/model routing, Evidence
  projection, citation, and Replay behavior.
- Static error/event contracts, OpenAPI, both SDKs, Workbench, PostgreSQL integration,
  migrations, Compose, and Helm remain release gates.

## Executed gate evidence

- Phase 12 targeted Knowledge and replay tests passed (2 tests).
- The Phase 12 freeze release gate passed with 345 Python tests and 18 default skips;
  static, SDK, frontend, OpenAPI, Compose, and Helm checks were green.
- A clean PostgreSQL/pgvector database upgraded through the full Alembic chain,
  reported no drift, and passed 15 integration tests with three destructive migration
  tests intentionally skipped.

## Human review checklist

- Confirm the authoritative ACL sources, deny semantics, and classification mapping for
  each production document source.
- Confirm parser coverage, maximum chunk size, heading extraction, checksum/version
  retention, and the embedding profile/region approved for each data class.
- Confirm PostgreSQL/pgvector capacity and failure recovery cannot expose partial or
  stale ACL-filtered indexes.
- Confirm Knowledge Evidence and citations are sufficient for the KnowledgeAgent gate,
  including an explicit unknown answer when authorized evidence is absent.
