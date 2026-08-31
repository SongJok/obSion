# ADR 0048: WeCom EncodingAESKey decrypt is an Experience inbound concern

- Status: Accepted
- Date: 2026-08-30
- Amends: ADR 0011 and ADR 0016 for WeCom ciphertext

## Context

WeCom callbacks often deliver only an `Encrypt` field. Without
`EncodingAESKey`, Obsion correctly failed closed rather than invent plaintext.
After DingTalk/WeCom HTTP delivery (ADR 0047), inbound ciphertext remained the
last vendor Experience gap that blocked real WeCom loopback ingest.

Decrypting WeCom packages is not a Capability and must not place AES keys in
Agent context or TOML.

## Decision

When `OBSION_WECOM_ENCODING_AES_KEY` is set, the IM Experience adapter:

- verifies `msg_signature` with `OBSION_WECOM_TOKEN` when present
  (`sha1(sort(token, timestamp, nonce, encrypt))`);
- decrypts AES-256-CBC with IV = key[:16] using the documented
  `random(16) + msg_len(4) + msg + receiveid` package;
- optionally checks `receiveid` against `OBSION_WECOM_CORP_ID`;
- parses the decrypted XML into the existing WeCom inbound envelope;
- treats a bare decrypted echostr as `UrlVerification` without creating a Turn.

Without EncodingAESKey, ciphertext still fails closed. Generic `--deliver http`
remains rejected. Public WeCom ingress stays unimplemented.

## Consequences

Operators can decrypt WeCom ciphertext on loopback and ingest paths without a
second Harness. Live EncodingAESKey remains operator-owned. Public WeCom TLS
hosting is a later phase.
