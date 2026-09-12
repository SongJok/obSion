# ADR 0129 — Preserve legacy unknown delivery state across migrations

- Date: 2026-09-12.
- Status: implemented; local PostgreSQL round trip passed and ordinary development upgraded; remote CI pending.
- Phase 98 productization increment; no application or external delivery API change.

The first main-branch CI execution exposed an IM migration test failure. Historical
PENDING/FAILED deliveries are converted to UNKNOWN by `a82c03d14e25`, which predates
`reconciliation_required_at`. Later trigger guards require that marker even for
updates that keep UNKNOWN. The existing test also manufactured new states by
updating status alone under the current guards, which correctly reject UNKNOWN to
PENDING without reconciliation.

Add data-only migration `b4c6d8e0f2a4` after `a3b5c7d9e1f2`. It sets the marker to
the migration transaction time only where UNKNOWN lacks it. The timestamp means
reconciliation became required; it is not a vendor delivery time. Status, receipts,
actual delivery timestamps and send counters are preserved. Existing guards stay
active; other inconsistent receipt fields cause a transactional failure rather
than being erased. Downgrade retains the marker because removing it would restore
an invalid state. Re-upgrade is idempotent. No message is sent or made retryable.

Also correct the historical `c9d1e4f6a2b3` downgrade: its predecessor already allows
UNKNOWN, so the restored constraint must include it. Previously it raised an opaque
constraint error before the older explicit reconciliation barrier could run. This
is a repair of the downgrade implementation, not a change to a deployed upgrade.
The `a82c03d14e25` rule still refuses every non-SENT row before downgrading to a
version that could resend it.

The destructive test owns a fresh temporary PostgreSQL database. It seeds each
historical state on its corresponding old schema, upgrades to the current head,
and checks the unchanged downgrade refusal and transaction rollback. It additionally
checks missing-marker repair, receipt/counter preservation, idempotence and the
current trigger's rejection of an unproved UNKNOWN-to-PENDING update. Synthetic
manual reconciliation remains explicitly marked; it is not real vendor evidence.
A real ordinary-development backup was restored and upgraded in a disposable clone
with row counts and delivery metadata preserved. Ordinary development is now upgraded
to b4c6d8e0f2a4; six authorized managed documents remain available.
