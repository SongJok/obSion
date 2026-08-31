# ADR 0016: IM webhook hosting is loopback-only

- Status: Amended by ADR 0046 for explicit public TLS Feishu ingress
- Date: 2026-08-29

## Context

Phase 32/36 translate documented Feishu, DingTalk, and WeCom envelopes and render
local-outbox replies. Operators still had to pipe JSON into `obsion-im serve` on stdin.
Vendors POST callbacks over HTTP. Binding `0.0.0.0` or calling vendor hosts without a
tenant application would either expose a public unauthenticated ingest surface or fake
an external integration. WeCom AES ciphertext cannot be decrypted without
`EncodingAESKey`.

## Decision

`obsion-im serve --listen 127.0.0.1[:port]` hosts a loopback webhook. It accepts the
same documented envelopes as stdin serve. Feishu `url_verification` returns the
challenge without `OBSION_TOKEN`. Message ingest still requires a token and
`im_principal_bindings`. Bind addresses other than `127.0.0.1` fail closed. Outbound
delivery remains `local_outbox`; `--deliver http` remains rejected. WeCom AES
ciphertext (`Encrypt` without plaintext `Content` / `FromUserName`) fails closed.

The adapter still must not import vendor SDKs or HTTP clients, and source must not
contain vendor endpoint strings. Stdlib `http.server` is allowed only as a loopback
listener.

## Consequences

Local callback testing no longer requires stdin plumbing. Public webhook hosting,
WeCom AES decrypt, and vendor HTTP POST remain unimplemented until a real tenant
application and operator-owned ingress exist.
