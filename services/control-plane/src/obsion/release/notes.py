"""Machine-verifiable release-note contracts.

Release notes are operational inputs, not marketing copy.  This validator keeps
the declared phase range, migration posture, pinned vendor origins, secret-name
references, rollout checks, rollback steps, and source documents reviewable in
CI without loading any credential values.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"^OBSION_[A-Z0-9_]+$")
_MIGRATION_MODES = frozenset({"none", "alembic"})


class ReleaseNotesError(ValueError):
    """Raised when a release-note manifest is incomplete or unsafe."""


def validate_release_notes(path: Path, repository_root: Path) -> dict[str, Any]:
    """Validate one release-note manifest and return a bounded summary."""

    document = _load_mapping(path)
    if document.get("apiVersion") != "obsion.ai/v1":
        raise ReleaseNotesError("release notes apiVersion must be obsion.ai/v1")
    if document.get("kind") != "ReleaseNotes":
        raise ReleaseNotesError("release notes kind must be ReleaseNotes")

    metadata = _mapping(document, "metadata")
    name = _string(metadata, "name")
    version = _string(metadata, "version")
    phase = _positive_integer(metadata, "phase")
    if not _NAME_PATTERN.fullmatch(name):
        raise ReleaseNotesError("release notes metadata.name must be a lowercase slug")
    if not _VERSION_PATTERN.fullmatch(version):
        raise ReleaseNotesError("release notes metadata.version must be semantic")

    spec = _mapping(document, "spec")
    consolidates = _integer_list(spec, "consolidates")
    expected = list(range(consolidates[0], consolidates[-1] + 1))
    if consolidates != expected:
        raise ReleaseNotesError("release notes consolidated phases must be contiguous")
    if consolidates[-1] != phase - 1:
        raise ReleaseNotesError("release notes must consolidate through the preceding phase")

    migration = _mapping(spec, "migration")
    migration_mode = _string(migration, "database")
    if migration_mode not in _MIGRATION_MODES:
        raise ReleaseNotesError("release notes migration.database must be none or alembic")
    revisions = _string_list(migration, "revisions", allow_empty=True)
    if migration_mode == "none" and revisions:
        raise ReleaseNotesError("a no-migration release cannot declare Alembic revisions")
    if migration_mode == "alembic" and not revisions:
        raise ReleaseNotesError("an Alembic release must declare at least one revision")
    _string(migration, "notes")

    documents = _string_list(spec, "documents")
    resolved_root = repository_root.resolve()
    for document_path in documents:
        _validate_document_path(document_path, resolved_root)

    rollout = _string_list(spec, "rollout")
    rollback = _string_list(spec, "rollback")
    verification = _string_list(spec, "verification")
    limitations = _string_list(spec, "knownLimitations")
    boundaries = _string_list(spec, "boundaries")

    repository_summary: dict[str, Any] = {}
    repository_evidence = spec.get("repositoryEvidence")
    if repository_evidence is not None:
        if not isinstance(repository_evidence, dict):
            raise ReleaseNotesError("release notes repositoryEvidence must be an object")
        repository_summary = _validate_repository_evidence(
            repository_evidence,
            repository_root=resolved_root,
            version=version,
            phase=phase,
            consolidates=consolidates,
            migration_revisions=revisions,
        )

    vendors_raw = spec.get("vendors")
    if not isinstance(vendors_raw, list) or not vendors_raw:
        raise ReleaseNotesError("release notes spec.vendors must be a non-empty list")
    vendors: list[str] = []
    environment_names: set[str] = set()
    for index, item in enumerate(vendors_raw):
        if not isinstance(item, dict):
            raise ReleaseNotesError(f"release notes vendor at index {index} must be an object")
        vendor = _string(item, "id")
        if not _NAME_PATTERN.fullmatch(vendor) or vendor in vendors:
            raise ReleaseNotesError("release notes vendor ids must be unique lowercase slugs")
        vendors.append(vendor)

        names = _string_list(item, "environmentVariables")
        for environment_name in names:
            if not _ENVIRONMENT_NAME_PATTERN.fullmatch(environment_name):
                raise ReleaseNotesError(
                    "release notes may contain environment variable names only, not values"
                )
            environment_names.add(environment_name)

        experience = item.get("experience")
        knowledge = item.get("knowledge")
        if experience is None and knowledge is None:
            raise ReleaseNotesError(
                "release notes vendors require experience or knowledge contract"
            )
        if experience is not None:
            if not isinstance(experience, dict):
                raise ReleaseNotesError("release notes vendor experience must be an object")
            _string(experience, "outboundTransport")
            _validate_origin(_string(experience, "outboundOrigin"))
            _string_list(experience, "inboundSecurity")
        if knowledge is not None:
            if not isinstance(knowledge, dict):
                raise ReleaseNotesError("release notes vendor knowledge must be an object")
            _string(knowledge, "connector")
            _validate_origin(_string(knowledge, "origin"))
            _string_list(knowledge, "operations")

    return {
        "name": name,
        "version": version,
        "phase": phase,
        "consolidates": consolidates,
        "database_migration": migration_mode,
        "vendors": vendors,
        "environment_variables": sorted(environment_names),
        "documents": len(documents),
        "rollout_steps": len(rollout),
        "rollback_steps": len(rollback),
        "verification_checks": len(verification),
        "known_limitations": len(limitations),
        "boundaries": len(boundaries),
        **repository_summary,
    }


def read_project_version(path: Path) -> str:
    """Read the authoritative project release version for build metadata."""

    document = _load_mapping(path)
    version = document.get("version")
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise ReleaseNotesError("project status version must be semantic")
    return version


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseNotesError(f"unable to load release document {path}") from exc
    if not isinstance(document, dict):
        raise ReleaseNotesError(f"release document {path} must be an object")
    return document


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ReleaseNotesError(f"release notes {key} must be an object")
    return value


def _string(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReleaseNotesError(f"release notes {key} must be a non-empty string")
    return value.strip()


def _positive_integer(parent: dict[str, Any], key: str) -> int:
    value = parent.get(key)
    if type(value) is not int or value < 1:
        raise ReleaseNotesError(f"release notes {key} must be a positive integer")
    return value


def _integer_list(parent: dict[str, Any], key: str) -> list[int]:
    value = parent.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not int or item < 1 for item in value)
    ):
        raise ReleaseNotesError(f"release notes {key} must be a list of positive integers")
    if len(set(value)) != len(value):
        raise ReleaseNotesError(f"release notes {key} must not contain duplicates")
    return value


def _string_list(parent: dict[str, Any], key: str, *, allow_empty: bool = False) -> list[str]:
    value = parent.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ReleaseNotesError(f"release notes {key} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReleaseNotesError(f"release notes {key} must contain non-empty strings")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise ReleaseNotesError(f"release notes {key} must not contain duplicates")
    return normalized


def _validate_origin(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseNotesError("release note vendor origins must be bare HTTPS origins")


def _validate_document_path(value: str, repository_root: Path) -> None:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseNotesError("release note document paths must stay inside the repository")
    resolved = (repository_root / relative).resolve()
    try:
        resolved.relative_to(repository_root)
    except ValueError as exc:
        raise ReleaseNotesError(
            "release note document paths must stay inside the repository"
        ) from exc
    if not resolved.is_file():
        raise ReleaseNotesError(f"release note document does not exist: {value}")


def _validate_repository_evidence(
    evidence: dict[str, Any],
    *,
    repository_root: Path,
    version: str,
    phase: int,
    consolidates: list[int],
    migration_revisions: list[str],
) -> dict[str, Any]:
    project_status_path = _repository_path(
        _string(evidence, "projectStatus"), repository_root, directory=False
    )
    project_status = _load_mapping(project_status_path)
    if project_status.get("version") != version:
        raise ReleaseNotesError("release notes version must match project status")
    expected_phases = [f"phase-{value:02d}" for value in range(1, phase + 1)]
    if project_status.get("completed_phases") != expected_phases:
        raise ReleaseNotesError(
            "project status completed phases must be contiguous through release"
        )
    if project_status.get("current_phase") != f"phase-{phase:02d}":
        raise ReleaseNotesError("project status current phase must match release phase")

    expected_consolidates = list(range(1, phase))
    if consolidates != expected_consolidates:
        raise ReleaseNotesError(
            "repository-wide release notes must consolidate every preceding phase"
        )

    reports_directory = _repository_path(
        _string(evidence, "phaseReportsDirectory"), repository_root, directory=True
    )
    missing_reports = [
        value
        for value in range(1, phase + 1)
        if not (reports_directory / f"PHASE-{value:02d}-REPORT.md").is_file()
    ]
    if missing_reports:
        raise ReleaseNotesError(
            "release phase reports are incomplete: "
            + ", ".join(str(value) for value in missing_reports)
        )

    architecture_directory = _repository_path(
        _string(evidence, "architectureDirectory"), repository_root, directory=True
    )
    invalid_architecture = [
        value
        for value in range(1, phase + 1)
        if len(list(architecture_directory.glob(f"phase-{value}-*.md"))) != 1
    ]
    if invalid_architecture:
        raise ReleaseNotesError(
            "release architecture reviews must contain exactly one document per phase: "
            + ", ".join(str(value) for value in invalid_architecture)
        )

    versions_directory = _repository_path(
        _string(evidence, "alembicVersionsDirectory"), repository_root, directory=True
    )
    actual_revisions = _alembic_revision_chain(versions_directory)
    if migration_revisions != actual_revisions:
        raise ReleaseNotesError("release Alembic revisions must equal the linear repository chain")

    sbom_path = _repository_path(_string(evidence, "sbom"), repository_root, directory=False)
    try:
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseNotesError("release SBOM must be valid JSON") from exc
    component = sbom.get("metadata", {}).get("component", {})
    if not isinstance(component, dict) or component.get("version") != version:
        raise ReleaseNotesError("release SBOM component version must match release version")

    publication = _mapping(evidence, "publication")
    stage = _string(publication, "stage")
    if stage not in {"alpha", "beta", "rc", "stable"}:
        raise ReleaseNotesError("release publication stage is invalid")
    externally_published = _boolean(publication, "externallyPublished")
    signed_tag = _boolean(publication, "signedTag")
    if stage == "alpha" and (externally_published or signed_tag):
        raise ReleaseNotesError(
            "repository Alpha contract cannot claim an external publication or signed tag"
        )

    return {
        "release_stage": stage,
        "phase_reports": phase,
        "architecture_reviews": phase,
        "migration_head": actual_revisions[-1],
        "migration_revisions": len(actual_revisions),
        "sbom": sbom_path.relative_to(repository_root).as_posix(),
        "externally_published": externally_published,
        "signed_tag": signed_tag,
    }


def _repository_path(value: str, repository_root: Path, *, directory: bool) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseNotesError("release repository evidence paths must stay inside repository")
    resolved = (repository_root / relative).resolve()
    try:
        resolved.relative_to(repository_root)
    except ValueError as exc:
        raise ReleaseNotesError(
            "release repository evidence paths must stay inside repository"
        ) from exc
    valid = resolved.is_dir() if directory else resolved.is_file()
    if not valid:
        kind = "directory" if directory else "file"
        raise ReleaseNotesError(f"release repository evidence {kind} does not exist: {value}")
    return resolved


def _alembic_revision_chain(directory: Path) -> list[str]:
    parents: dict[str, str | None] = {}
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise ReleaseNotesError(f"unable to inspect Alembic revision {path.name}") from exc
        revision = _module_literal(tree, "revision")
        parent = _module_literal(tree, "down_revision")
        if not isinstance(revision, str) or not revision:
            raise ReleaseNotesError(f"Alembic revision is invalid in {path.name}")
        if parent is not None and not isinstance(parent, str):
            raise ReleaseNotesError("release Alembic chain must not contain branches or merges")
        if revision in parents:
            raise ReleaseNotesError(f"duplicate Alembic revision {revision}")
        parents[revision] = parent
    if not parents:
        raise ReleaseNotesError("release Alembic directory is empty")
    bases = [revision for revision, parent in parents.items() if parent is None]
    referenced = {parent for parent in parents.values() if parent is not None}
    heads = sorted(set(parents) - referenced)
    if len(bases) != 1 or len(heads) != 1:
        raise ReleaseNotesError("release Alembic history must have one base and one head")
    chain: list[str] = []
    current: str | None = heads[0]
    seen: set[str] = set()
    while current is not None:
        if current in seen or current not in parents:
            raise ReleaseNotesError("release Alembic history is cyclic or incomplete")
        seen.add(current)
        chain.append(current)
        current = parents[current]
    chain.reverse()
    if len(chain) != len(parents):
        raise ReleaseNotesError("release Alembic history contains disconnected revisions")
    return chain


def _module_literal(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError) as exc:
                raise ReleaseNotesError(f"Alembic {name} must be a literal") from exc
    raise ReleaseNotesError(f"Alembic revision is missing {name}")


def _boolean(parent: dict[str, Any], key: str) -> bool:
    value = parent.get(key)
    if type(value) is not bool:
        raise ReleaseNotesError(f"release notes {key} must be a boolean")
    return value
