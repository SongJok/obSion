# PHASE-12-REPORT — ACL-preserving Knowledge pipeline

> Retrospective Phase 80 record based on the implemented ingestion/retrieval contract;
> it does not create source-owner or data-governance approval.

## Delivered

- Added versioned document ingestion, bounded structured chunks, parser/checksum
  metadata, explicit ACL normalization, and optional Model-Gateway embeddings.
- Enforced organization/version/chunk authorization before lexical/vector ranking,
  with deny precedence and zero-recall unauthorized behavior.
- Normalized search results into DOCUMENT Evidence and governed download/detail paths.

## Migration and validation

Document, version, chunk, grant, and vector state is covered by the linear Alembic
chain including pgvector revisions. Phase 80 reran ACL tightening, ranking, parser,
Evidence, citation, Replay, and PostgreSQL gates.

## Remaining boundary

Missing ACL fails closed; Obsion never invents organization-wide document access.
