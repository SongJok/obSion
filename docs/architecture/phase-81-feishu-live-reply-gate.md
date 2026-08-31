# Phase 81 Feishu live reply validation review

## Review question

Can operators prove the real `feishu-http` delivery leg against a live tenant—after
the Alpha.1 candidate contract—without guessing a send target, fabricating Harness
state, or letting any credential or vendor error leak?

**Status: PENDING — live probes are operator-owned and a skipped probe is never a
passed validation; this gate does not claim a DingTalk/WeCom live delivery or a
full Harness loop against a tenant.**

## Delivery contract

- `FeishuClient.list_chats` is read-only, bounded to one vendor page of at most 100
  items, requires a tenant token, and fails closed on malformed items with redacted
  errors. `FeishuHttpChannel` exposes the same listing.
- `make validate-feishu-live` runs four non-sending probes: tenant authentication,
  bot chat listing, nonexistent document failure closure, and wiki-space
  read/denial.
- `make validate-feishu-send-live` requires `OBSION_FEISHU_SEND_LIVE=1`, app
  credentials, and an explicit `OBSION_FEISHU_LIVE_CHAT_ID`; it sends exactly one
  marked probe message through `FeishuHttpChannel.reply` and asserts the vendor
  message id. The probe never auto-discovers a target.
- The send probe is transport validation only: no Run, Turn, Event, Evidence,
  Approval, or audit row is created, and the production delivery path still
  requires a Policy-authorized receipt.
- `docs/release/0.81.0-dev.yaml` is the machine-validated contract; the frozen
  `0.80.0-alpha.1` contract remains valid as a static historical record whose
  live-tree evidence was verified at the Alpha.1 candidate commit.

## Automated acceptance map

- `apps/im-adapter/tests/test_feishu.py` covers bounded chat listing, page-size
  limits, missing/malformed items, error redaction, and both opt-in live probes.
- `test_phase76_feishu_live_validation.py` proves the non-sending target remains
  gated and now enumerates four probes.
- `test_phase81_feishu_live_reply.py` proves the send target's explicit gates,
  marker registration, environment documentation, CLI default manifest, and
  fail-closed manifest drift.
- `test_phase80_alpha1_release.py` proves the frozen Alpha.1 contract stays valid
  and secret-free after the development line moved on.
- `make check`, secret scanning, and release-note validation cover the revision.

## Migration review

Phase 81 adds no database revision and no Event version. Rollback is unsetting the
live environment variables; no data change is possible because the probes create no
durable rows.

## Human review checklist

- Confirm live validation used operator-owned credentials and an operator-chosen
  test chat, and that the shell environment was cleaned afterwards.
- Verify the marked probe message arrived exactly once and no retry followed a
  successful delivery.
- Keep DingTalk/WeCom live delivery, generic HTTP delivery, and every production
  write path denied.
