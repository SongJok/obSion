# ADR 0126 — Preserve verified DingTalk list and table structure

- Status: accepted for development; parser deployment verified, broader answer quality incomplete.
- Date: 2026-09-12.
- Builds on ADR 0114–0125.

The ordinary source worker found real text documents that could not be used because
their numbering, merged table cells and duplicate code representation were not
understood. Removing completeness gaps without preserving those relationships could
produce an answer associating a cost with the wrong category. The existing generic
chunker could also split a correctly extracted table or copy its tail into a separate
retrieval result.

`dingtalk-jsonml-v2` supports explicit decimal and lower-letter list formats with a
verified group identifier, level, label template and initial counter. Child counters
reset after their parent advances; separate groups restart independently. Unknown
formats, hidden symbols, invalid counters and orphan nesting remain incomplete.
Only the documented explicit format is interpreted; opaque preset metadata is not
flattened into body evidence. A code attribute and an exactly equal rendered body
are emitted once; conflicting representations remain incomplete.

Merged tables require a bounded rectangular physical grid. Every covered coordinate
must contain an empty hidden placeholder. Overlap, missing placeholders, invalid
span types, out-of-bounds coordinates and hidden content prevent publication. Valid
merged cells and explicit header rows use escaped HTML with only generated tags and
integer spans; no source URLs or arbitrary attributes are copied. A single unmerged
row marked `sr=true` is preserved as separate columns, rather than interpreted as a
data table. Ordinary unmerged data tables retain their existing text representation.

Managed v2 ingestion keeps each generated table or column block as a complete
retrieval chunk, including internal paragraph breaks. It does not copy a layout
tail into another chunk. Layouts larger than the existing 1,400-character evidence
budget remain explicitly incomplete pending a larger-table contract. The JSONML
byte, depth, element and rendered-output budgets remain bounded. Other sources keep
the existing chunking behavior. Source revision, raw checksum and parser version
remain durable provenance; an updated parser creates a new document version even
when the vendor revision is unchanged. No schema migration is added; the existing
`a3b5c7d9e1f2` head is required.

The [official DingTalk JSONML schema](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/skills/multi/dingtalk-doc/references/doc/format/doc-jsonml-schema.md)
defines list identifiers, levels, initial counters, table spans and column-layout
markers. Four private real payloads were obtained through Gateway/Policy with
permission checks before and after each read. Their hidden placeholder geometry
and exact duplicate code were independently inspected. Private payloads stay in
ignored local validation storage; evidence ledgers contain hashes and outcomes.

Failure-first tests reproduced seven failures. Tests now assert exact list labels,
merged-cell coordinates, escaped contents, header roles, column separation, duplicate
code handling, incomplete unsupported elements and unsplit evidence chunks. A worker
integration test covers valid version replacement, subsequent invalid geometry
revoking access to the old body, and recovery with the same document identifier.
It also passes against disposable PostgreSQL after migration and schema-drift check.

The ordinary worker was pinned to an immutable copy of ADR 0125 source before editing,
so unvalidated source files could not be loaded on a supervisor restart. Deployment
must update both the API package and the worker snapshot after validation. Rollback
requires pausing the source before reverting both, then fresh permission/body checks;
it must not leave v2-only documents readable under an older parser.

This is a bounded source-quality increment, not complete document-format coverage.
Images, cards, mentions, AI tables and empty documents remain explicit gaps. A
syntactically complete template containing `xx`, `xxx` or “添加具体内容” does not establish
an actual policy value. Real Q&A must verify this distinction, as well as delivery.

Final local regression passed 2579 tests, with 252 explicit skips and 7 live deselections, in 636.10 seconds. The final focused combination passed 85 tests in 56.20 seconds, and all 272 Python source files passed strict typing. The normal API matches all 247 packaged Python files, and the pinned worker now makes six authorized documents available. Five actual K3 calls cover table understanding and two Pointbot cases. The table answer has supported facts but exposes internal identifiers; a numbered-contract question was withheld despite supported claims. Those quality failures, one missing observed ingress and a measured delay before handler entry remain open in the [validation record](../phases/productization-dingtalk-layout-validation.md).
