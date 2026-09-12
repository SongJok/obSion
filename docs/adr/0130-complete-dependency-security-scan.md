# ADR 0130 — Scan all Python workspace dependencies and apply security patches

- Date: 2026-09-12.
- Status: implemented; dependency scans, frontend and full Python regression passed; ordinary runtime deployed, remote CI pending.
- Phase 98 productization; no model, permission or public API contract change.

After the missing Trivy action reference was fixed, remote CI reached the security
scan and failed. Local scanning of the same committed source using verified Trivy
0.70.0 and its current official database reproduced HIGH/CRITICAL findings in
Next.js 16.3.2 and sharp 0.35.3. Trivy also reported that it could not parse the uv
workspace lock because it contains multiple root packages. An otherwise successful
filesystem scan therefore did not establish Python dependency coverage.

The existing CycloneDX generation enumerates every locked Python package with its
PyPI purl and exact version. Add an explicit Trivy SBOM scan immediately after the
filesystem scan, using the generated report. It retains HIGH/CRITICAL, failure exit
code 1 and ignore-unfixed=false. A contract test checks generation before scanning
and the identical report path. Filesystem and container scanning remain enabled.
The complete Python SBOM revealed two HIGH advisories for pypdf 5.9.0 that the old
filesystem scan missed. No dependency is suppressed to make the gate green.

Upgrade Next.js and its ESLint configuration to 16.3.3, sharp to 0.35.4, and pypdf
to locked 6.14.2 with a supported dependency range >=6.14.2,<7. The pypdf dependency
moves a major version; the public parser contract remains PdfReader/pages/extract_text
and must pass document and full Python regression before promotion. No historical
parser output or evidence is rewritten. npm audit also identified a HIGH development
dependency advisory in js-yaml, now locked to 4.3.2. Other Python package versions
are unchanged; transitive Next.js/native-image packages follow the corrected lock.

Official references: [Next.js advisory](https://github.com/advisories/GHSA-2xp9-vwfh-vxw4),
[sharp advisory](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c),
[pypdf release](https://github.com/py-pdf/pypdf/releases/tag/6.14.2), and
[js-yaml advisory](https://github.com/advisories/GHSA-2883-xcg3-v3hh).

Local scan evidence uses official database layer
`sha256:827840441a2d1d960f93714c70cfa35f5e0020e9bb8737556fa752203a591c51`,
verified before extraction. Fresh filesystem and full Python SBOM scans return no
HIGH/CRITICAL findings. This is dependency coverage at that database snapshot, not
proof of exploit resistance or production readiness. Runtime images must be rebuilt
and verified with the new locked packages; changing a lock alone is not deployment.
