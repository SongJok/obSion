# ADR 0127 — Align answer review with platform citations

- Status: accepted for development; integrated regression and ordinary deployment verified.
- Date: 2026-09-12.
- Builds on ADR 0112, 0118–0124 and 0126.

Two real ADR 0126 answers exposed a contract inconsistency: the author must leave
citations to the platform, but the independent reviewer sees the answer before
those citations are appended and must assess the user's source-attribution request.
One answer exposed internal source pointers; another had supported claims but was
rejected overall. The old summary discarded the two aggregate review decisions,
so the exact cause of that historical rejection cannot be established.

Both trusted contracts now state that verified platform citations fulfill source
attribution. The reviewer still receives only document bodies, not untrusted titles
or metadata. Missing author-generated citations alone do not reject the candidate;
facts, completeness, scope and other requested format constraints still apply.
Acceptance still requires every claim to be supported, exact locally validated
body quotations, and both `answer_supported` and `question_answered` to be true.
The existing summary adds those optional booleans only after the complete review
schema, quotation and truncation checks pass. Invalid or unavailable reviews record
null, without retaining model prose or raw quotations in diagnostics.

Knowledge answer presentation has one bounded repair attempt. A local detector
identifies actual Evidence IDs and field/value source pointers in the candidate,
while preserving identifiers appearing literally in document bodies. The platform
rechecks current source access and regenerates from the same question and evidence
under the remaining Model Gateway budget. It records the repair count and a local
reason, never the rejected prose. A second presentation failure is withheld; a clean
rewrite still needs the ordinary critic, independent factual review and final
source-publication checks. This is not a semantic retry loop and does not claim
general autonomous project execution. GENERAL and engineering routes are unchanged.

Real PostgreSQL observation also found result reads waiting behind a model Run row
lock: their PolicyDecision insert used a foreign key to that Run. Following ADR
0123, read-only historical-content checks omit that optional foreign key while
preserving the Policy resource's run ID and audit correlation. Mutation paths keep
the link. This covers run artifacts/events, workspace artifacts/evidence and thread
timeline; all source-authorization checks remain mandatory, including revoked
sources. There is no new schema migration; the existing `a3b5c7d9e1f2` head remains.

Failure-first PostgreSQL testing reproduced the artifact read lock. Ten real
PostgreSQL cases subsequently verify prompt reads under a held Run lock and content
denial after revocation. Twelve review diagnostic cases retain the unchanged
acceptance boundary. Five presentation cases cover literal identifiers, successful
repair, repeated failure, unsupported repair and access changing before retry.

Real governed zziv documents and four actual K3 calls produced verified contract
categories and table structure answers with clean platform citations. These runs
did not need presentation regeneration; that branch is currently synthetic test
evidence. The contract run preceded the final presentation/read implementation;
the finance run used the final implementation. During the latter, results returned
in under half a second without an observed database blocker. Timing is a local
sample, not a service-level guarantee. See the [validation record](../phases/productization-grounding-contract-validation.md).

Final integrated Python regression passed 2596 tests (262 skips, 7 live deselections)
in 704.80 seconds. Ordinary API and pinned worker use the same validated package;
248 API Python files match and all six documents remain available with stable IDs.
Two new Pointbot cases each used two successful K3 calls, passed full review and
were delivered once with exact artifact/chat equality. Both vendor receipts are
SUCCESS/UNREAD. One inbound message took 60.700 seconds to enter the local handler.
The user explicitly prioritizes quality over speed and requests governed multi-round
analysis and tool/document calls; this bounded presentation repair is one increment
toward that requirement, not completion of it.
