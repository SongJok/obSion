# ADR 0042: Feishu inbound uses official HTTP signatures

- Status: Accepted
- Date: 2026-08-30

## Context

Phase 32 verified a local JSON HMAC wrapper. Real Feishu event subscription POSTs
the raw event body with `X-Lark-Request-Timestamp`, `X-Lark-Request-Nonce`, and
`X-Lark-Signature`. When Encrypt Key is configured, Feishu also wraps the event
as AES-256-CBC. Treating the local wrapper as Feishu security, or accepting
unsigned events after an encrypt key is set, would fake the vendor protocol.

URL verification is documented as excluded from signature verification.

## Decision

`obsion-im` verifies official Feishu headers with SHA-256 over
`timestamp || nonce || encrypt_key || raw_body`. The encrypt key and optional
verification token come only from `OBSION_FEISHU_ENCRYPT_KEY` and
`OBSION_FEISHU_VERIFICATION_TOKEN`. Encrypted `{encrypt}` bodies are decrypted
with the documented AES-256-CBC construction after the signature matches.
URL verification may remain unsigned. When the encrypt key is unset, unsigned
fixtures and the local JSON wrapper remain for development ingest.

Webhook hosting stays loopback-only. WeCom AES and DingTalk HTTP remain
unimplemented. The adapter still does not implement Harness.

## Consequences

A tunneled `127.0.0.1` listener can accept real Feishu callbacks without a
second control plane. Public ingress remains operator-owned.
