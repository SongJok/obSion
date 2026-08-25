# Security policy

## Supported versions

Security fixes are provided for the latest minor release on the current major version.
Pre-release builds receive fixes on a best-effort basis and must not be treated as a
production security boundary.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository host's
private security-advisory feature and include the affected version, deployment model,
reproduction steps, impact, and any proposed mitigation. If private advisories are not
available on the mirror you use, contact an active maintainer privately using the
address in that maintainer's verified project profile.

Maintainers will acknowledge a complete report within three business days, provide an
initial assessment within seven business days, and coordinate disclosure after a fix
is available. We ask reporters to allow up to 90 days for remediation unless active
exploitation requires an accelerated disclosure.

## Deployment responsibility

Production deployments must use OIDC authentication, external secret management,
TLS, a read-only database identity for query capabilities, tenant-scoped policies,
network egress controls, and immutable audit storage. Development authentication and
the example credentials in `.env.example` are intentionally rejected in production.

See `docs/security/security-model.md` for the trust model and required controls.
