# ADR 0122 — Preserve verified source access during routine refresh

- Status: accepted as a development increment; live acceptance is recorded separately.
- Date: 2026-09-12
- Builds on: ADR 0115–0121.

## Observed problem

A real Kimi answer using the synchronized zziv welcome document passed its first source check but was withheld before review. The recurring worker had reset every discovered document to PENDING and cleared its unexpired access lease. Directory scheduling and access authorization shared one status field. With periodic discovery running in the background, ordinary answers and historical reads could intermittently fail despite no observed revocation.

## Decision

Add a durable, nonnegative `KnowledgeSyncItem.read_generation`. Directory discovery records `seen_generation`; body acceptance or a failed body attempt records `read_generation`. The read stage selects all current-generation adoc items whose read attempt has not completed in that generation, including READY items. Reconstructing a worker or restarting its process does not lose or repeat a completed attempt within a scan. A later generation attempts them again.

Rediscovering an existing READY adoc in the same workspace preserves its status and exact existing access expiry. It neither grants access nor extends the lease. A changed kind or workspace still invalidates that access. Newly discovered or previously non-ready entries retain the established pending/partial handling. Only a successful governed body and permission read can renew access. Expiry, a failed or denied read, incomplete content, source pause, changed connector/identity/Policy, and missing documents still fail closed through the existing access predicate and checkpoints.

Deletion is marked only after the directory and document queue is exhausted. A document not yet rediscovered remains subject to its original lease until the scan establishes absence or that lease expires. Directory-level failures retain the existing invalidation behavior. This increment does not lengthen the five-minute remote verification lease or cache authorization indefinitely.

## Migration and compatibility

Migration `a3b5c7d9e1f2` follows `f2a4b6c8d0e1` and adds the integer scheduling column with a database default of zero and a nonnegative constraint. Existing access facts and document bodies are unchanged. Zero makes existing records eligible for a read in a nonzero current or subsequent scan. The ORM and database defaults agree; schema-drift validation caught and corrected the initial mismatch.

No public API or event schema changes and no new error codes. The existing source fence conservatively protects the new column; its authorization triggers and lease predicates are retained. Stop workers before migrating or rolling back. Downgrade removes only the added constraint and column; when reverting to the older scheduler, pause and resume sources to request a clean scan before relying on its refresh progress. Historical documents are not deleted.

## Validation and limits

A failure-first periodic-refresh test reproduces READY becoming PENDING during directory discovery. The corrected test covers two full scans with worker reconstruction at every step, verifies that discovery does not renew the lease, and requires another governed body read before renewal. Other cases verify natural expiry, denial, incomplete content, pause and missing-document handling.

The same six scenarios run in isolated PostgreSQL databases. Empty and populated migration round trips verify unchanged document identifiers, statuses, check times and expiry times, a zero scheduling default, negative-value rejection and no schema drift. Real development API, Redis, PostgreSQL, MinIO, the official host worker and Kimi calls supply separate live evidence; their exact outcomes belong in the phase report and ledger. Synthetic worker tests are not counted as live model or DingTalk acceptance.

The ordinary running environment is not upgraded by this increment. Complete format coverage, large-corpus refresh capacity, Pointbot document-answer delivery and complete enterprise autonomy remain separate acceptance requirements.
