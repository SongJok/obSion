# ADR 0035: Workspace Files are a path-versioned Artifact ledger

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Workbench and Workspace surfaces list Files beside Artifacts, Reports, and
Runtime. Uploads already created `ArtifactKind.FILE` blobs, but they had no
addressable path and no successor version. Operators could not say
`/notes/runbook.txt`. Putting every upload into Context Builder as SYSTEM text
would be prompt injection by architecture.

## Decision

Artifacts gain optional `path`, `file_version`, and `superseded_at`. A path is a
POSIX-style absolute file path with safe segments only. The same workspace path
increments `file_version` and supersedes the previous current row. Untitled
uploads remain valid artifacts and do not appear on `GET /workspaces/{id}/files`.

Files reuse the Artifact object store, workspace ACL, checksum, classification,
and audit. They do not become SYSTEM, AGENT, or Skill text unless a later Turn
attaches them through the existing untrusted Evidence path.

This is not a second object store, not a document editor, and not vendor IM HTTP.

## Consequences

The Files rail can list current paths and optional history. Replay and Harness
generated artifacts stay path-less. A later folder listing still has to treat
paths as data, not instructions.
