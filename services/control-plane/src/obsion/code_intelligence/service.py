from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from obsion.code_intelligence.parsers import PARSER_VERSION, module_name_for, parse_source_file
from obsion.common.errors import AuthorizationError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import (
    CodeGraphEdge,
    CodeRepository,
    CodeRepositoryGrant,
    CodeSnapshot,
    CodeSourceFile,
    CodeSymbol,
)
from obsion.domain.enums import Classification, CodeRelation, CodeSymbolKind
from obsion.security.identity import Principal

_CLASSIFICATION_LEVEL = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}
_ACL_LIST_KEYS = {
    "users",
    "roles",
    "departments",
    "deny_users",
    "deny_roles",
    "deny_departments",
}
_ACL_KEYS = _ACL_LIST_KEYS | {"organization"}
_GRAPH_OPERATIONS = frozenset({"code.symbol", "code.reference", "code.callers", "code.callees"})
_SEARCH_TOKEN = re.compile(r"[A-Za-z_][\w.]*|/[\w./-]+")


@dataclass(frozen=True, slots=True)
class SourceFileInput:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class SymbolHit:
    repository_id: UUID
    repository: str
    commit_id: str
    snapshot_id: UUID
    symbol_id: UUID
    path: str
    language: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    relations: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["repository_id"] = str(self.repository_id)
        payload["snapshot_id"] = str(self.snapshot_id)
        payload["symbol_id"] = str(self.symbol_id)
        payload["relations"] = [dict(item) for item in self.relations]
        return payload


def _authorized(principal: Principal, classification: Classification, acl: dict[str, Any]) -> bool:
    if str(principal.id) in set(acl.get("deny_users", [])):
        return False
    if principal.roles.intersection(acl.get("deny_roles", [])):
        return False
    if principal.department and principal.department in set(acl.get("deny_departments", [])):
        return False
    if str(principal.id) in set(acl.get("users", [])):
        return True
    if principal.roles.intersection(acl.get("roles", [])):
        return True
    if principal.department and principal.department in set(acl.get("departments", [])):
        return True
    if acl.get("organization") is True and _CLASSIFICATION_LEVEL[classification] <= 1:
        return True
    return principal.can(f"code.read.{classification.value.lower()}")


def _validate_acl(acl: dict[str, Any]) -> dict[str, Any]:
    unknown = set(acl) - _ACL_KEYS
    if unknown:
        raise ValidationError(
            "code_acl_invalid",
            "Repository ACL contains unsupported fields",
            fields=sorted(unknown),
        )
    if "organization" in acl and not isinstance(acl["organization"], bool):
        raise ValidationError("code_acl_invalid", "The organization ACL field must be a boolean")
    normalized: dict[str, Any] = {"organization": bool(acl.get("organization", False))}
    for key in _ACL_LIST_KEYS:
        value = acl.get(key, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValidationError(
                "code_acl_invalid", f"The {key} ACL field must be a list of strings"
            )
        normalized[key] = sorted({item.strip() for item in value})
    return normalized


def _grant_rows(
    *, organization_id: UUID, repository_id: UUID, acl: dict[str, Any]
) -> list[CodeRepositoryGrant]:
    subjects: set[tuple[str, str, str]] = set()
    for key, subject_type in (
        ("users", "USER"),
        ("roles", "ROLE"),
        ("departments", "DEPARTMENT"),
    ):
        subjects.update(("ALLOW", subject_type, value) for value in acl[key])
    for key, subject_type in (
        ("deny_users", "USER"),
        ("deny_roles", "ROLE"),
        ("deny_departments", "DEPARTMENT"),
    ):
        subjects.update(("DENY", subject_type, value) for value in acl[key])
    if acl["organization"]:
        subjects.add(("ALLOW", "ORGANIZATION", str(organization_id)))
    now = utc_now()
    return [
        CodeRepositoryGrant(
            organization_id=organization_id,
            repository_id=repository_id,
            effect=effect,
            subject_type=subject_type,
            subject_value=subject_value,
            created_at=now,
        )
        for effect, subject_type, subject_value in sorted(subjects)
    ]


def _subject_clause(
    grant: type[CodeRepositoryGrant], principal: Principal, *, include_organization: bool
) -> ColumnElement[bool]:
    clauses: list[ColumnElement[bool]] = [
        and_(grant.subject_type == "USER", grant.subject_value == str(principal.id))
    ]
    if principal.roles:
        clauses.append(
            and_(grant.subject_type == "ROLE", grant.subject_value.in_(sorted(principal.roles)))
        )
    if principal.department:
        clauses.append(
            and_(grant.subject_type == "DEPARTMENT", grant.subject_value == principal.department)
        )
    if include_organization:
        clauses.append(
            and_(
                grant.subject_type == "ORGANIZATION",
                grant.subject_value == str(principal.organization_id),
            )
        )
    return or_(*clauses)


def _authorization_clause(principal: Principal) -> ColumnElement[bool]:
    deny = exists(
        select(CodeRepositoryGrant.repository_id).where(
            CodeRepositoryGrant.repository_id == CodeRepository.id,
            CodeRepositoryGrant.organization_id == principal.organization_id,
            CodeRepositoryGrant.effect == "DENY",
            _subject_clause(CodeRepositoryGrant, principal, include_organization=False),
        )
    )
    direct_allow = exists(
        select(CodeRepositoryGrant.repository_id).where(
            CodeRepositoryGrant.repository_id == CodeRepository.id,
            CodeRepositoryGrant.organization_id == principal.organization_id,
            CodeRepositoryGrant.effect == "ALLOW",
            _subject_clause(CodeRepositoryGrant, principal, include_organization=False),
        )
    )
    organization_allow = and_(
        CodeRepository.classification.in_([Classification.PUBLIC, Classification.INTERNAL]),
        exists(
            select(CodeRepositoryGrant.repository_id).where(
                CodeRepositoryGrant.repository_id == CodeRepository.id,
                CodeRepositoryGrant.organization_id == principal.organization_id,
                CodeRepositoryGrant.effect == "ALLOW",
                CodeRepositoryGrant.subject_type == "ORGANIZATION",
                CodeRepositoryGrant.subject_value == str(principal.organization_id),
            )
        ),
    )
    permitted = [
        classification
        for classification in Classification
        if principal.can(f"code.read.{classification.value.lower()}")
    ]
    permission_allow: ColumnElement[bool] = (
        CodeRepository.classification.in_(permitted) if permitted else false()
    )
    return and_(
        CodeRepository.deleted_at.is_(None),
        ~deny,
        or_(direct_allow, organization_allow, permission_allow),
    )


def _symbol_rank(hit: SymbolHit, term: str) -> tuple[int, int, str]:
    needle = term.casefold()
    name = hit.name.casefold()
    qualified = hit.qualified_name.casefold()
    tokens = [token.casefold() for token in _SEARCH_TOKEN.findall(term) if len(token) >= 2]
    path_token = next((token for token in tokens if token.startswith("/")), "")
    if path_token and hit.kind == "API" and (path_token in name or path_token in qualified):
        return (0, -len(hit.name), qualified)
    if qualified == needle or name == needle:
        return (1, -len(hit.name), qualified)
    if hit.kind in {"FUNCTION", "METHOD"} and any(token in name for token in tokens):
        return (2, -len(hit.name), qualified)
    if hit.kind in {"CLASS", "TABLE"} and any(
        token in name or token in qualified for token in tokens
    ):
        return (3, -len(hit.name), qualified)
    return (4, -len(hit.name), qualified)


def _resolve_callee(to_name: str, symbols: dict[str, CodeSymbol]) -> CodeSymbol | None:
    if to_name in symbols:
        return symbols[to_name]
    parts = [part for part in to_name.split(".") if part]
    if parts and parts[0] in {"self", "cls", "super"}:
        parts = parts[1:]
    if not parts:
        return None
    simple = parts[-1]
    hint = parts[-2] if len(parts) >= 2 else None
    candidates = [
        symbol
        for symbol in symbols.values()
        if symbol.name == simple
        and symbol.kind in {CodeSymbolKind.FUNCTION, CodeSymbolKind.METHOD, CodeSymbolKind.CLASS}
    ]
    if hint:
        hinted = [
            symbol for symbol in candidates if hint.casefold() in symbol.qualified_name.casefold()
        ]
        if hinted:
            candidates = hinted
    if len(candidates) == 1:
        return candidates[0]
    suffix = ".".join(parts)
    suffixed = [symbol for symbol in candidates if symbol.qualified_name.endswith(suffix)]
    if len(suffixed) == 1:
        return suffixed[0]
    return None


class CodeIntelligenceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.max_files = settings.code_graph_max_files
        self.max_bytes = settings.code_graph_max_bytes

    async def upsert_repository(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        name: str,
        classification: Classification,
        acl: dict[str, Any],
        default_branch: str = "main",
    ) -> CodeRepository:
        if not principal.can("code.write"):
            raise AuthorizationError("code_write_denied", "Code Graph ingestion is not permitted")
        if not acl:
            raise ValidationError("code_acl_required", "An explicit repository ACL is required")
        acl = _validate_acl(acl)
        name = name.strip()
        if not name:
            raise ValidationError("code_acl_invalid", "Repository name is required")
        repository = await session.scalar(
            select(CodeRepository)
            .where(
                CodeRepository.organization_id == principal.organization_id,
                CodeRepository.name == name,
            )
            .with_for_update()
        )
        if repository is None:
            repository = CodeRepository(
                organization_id=principal.organization_id,
                name=name,
                default_branch=default_branch.strip() or "main",
                classification=classification,
                acl=acl,
            )
            session.add(repository)
            await session.flush()
        else:
            repository.classification = classification
            repository.acl = acl
            repository.default_branch = default_branch.strip() or repository.default_branch
            repository.deleted_at = None
        await session.execute(
            delete(CodeRepositoryGrant).where(
                CodeRepositoryGrant.organization_id == principal.organization_id,
                CodeRepositoryGrant.repository_id == repository.id,
            )
        )
        session.add_all(
            _grant_rows(
                organization_id=principal.organization_id,
                repository_id=repository.id,
                acl=acl,
            )
        )
        return repository

    async def index_snapshot(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        repository_id: UUID,
        commit_id: str,
        files: list[SourceFileInput],
    ) -> tuple[CodeRepository, CodeSnapshot]:
        if not principal.can("code.write"):
            raise AuthorizationError("code_write_denied", "Code Graph ingestion is not permitted")
        repository = await self._writable_repository(session, principal, repository_id)
        if not files:
            raise ValidationError(
                "code_snapshot_empty", "A Code Graph snapshot requires source files"
            )
        if len(files) > self.max_files:
            raise ValidationError(
                "code_snapshot_too_large",
                "The snapshot exceeds the file budget",
                max_files=self.max_files,
            )
        total_bytes = sum(len(item.content) for item in files)
        if total_bytes > self.max_bytes:
            raise ValidationError(
                "code_snapshot_too_large",
                "The snapshot exceeds the byte budget",
                max_bytes=self.max_bytes,
            )
        parsed = [parse_source_file(item.path, item.content) for item in files]
        checksum = hashlib.sha256(
            json.dumps(
                sorted((item.path, item.content_hash) for item in parsed),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        previous = None
        previous_hashes: dict[str, str] = {}
        if repository.current_snapshot_id is not None:
            previous = await session.scalar(
                select(CodeSnapshot).where(
                    CodeSnapshot.id == repository.current_snapshot_id,
                    CodeSnapshot.organization_id == principal.organization_id,
                )
            )
            if previous is not None and previous.content_checksum_sha256 == checksum:
                return repository, previous
            if previous is not None:
                previous_files = await self._previous_files(
                    session, principal.organization_id, previous
                )
                previous_hashes = {path: row.content_hash for path, row in previous_files.items()}
        ordinal = 1 if previous is None else previous.ordinal + 1
        reused = sum(1 for item in parsed if previous_hashes.get(item.path) == item.content_hash)
        snapshot = CodeSnapshot(
            id=new_id(),
            organization_id=principal.organization_id,
            repository_id=repository.id,
            ordinal=ordinal,
            commit_id=commit_id.strip() or "unspecified",
            parser_version=PARSER_VERSION,
            file_count=len(parsed),
            content_checksum_sha256=checksum,
            metadata_json={
                "reused_files": reused,
                "parse_errors": [item.path for item in parsed if item.parse_error],
            },
            created_at=utc_now(),
        )
        session.add(snapshot)
        await session.flush()
        symbol_rows: dict[str, CodeSymbol] = {}
        symbol_count = 0
        for item in parsed:
            source_file = CodeSourceFile(
                organization_id=principal.organization_id,
                snapshot_id=snapshot.id,
                repository_id=repository.id,
                path=item.path,
                language=item.language,
                content_hash=item.content_hash,
                size_bytes=item.size_bytes,
                parse_error=item.parse_error,
            )
            session.add(source_file)
            await session.flush()
            for symbol in item.symbols:
                row = CodeSymbol(
                    organization_id=principal.organization_id,
                    snapshot_id=snapshot.id,
                    repository_id=repository.id,
                    file_id=source_file.id,
                    kind=symbol.kind,
                    name=symbol.name[:400],
                    qualified_name=symbol.qualified_name[:1000],
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    signature=symbol.signature,
                    attributes=symbol.attributes,
                )
                session.add(row)
                await session.flush()
                symbol_rows[symbol.qualified_name] = row
                symbol_count += 1
        snapshot.symbol_count = symbol_count
        for item in parsed:
            for edge in item.edges:
                from_symbol = symbol_rows.get(edge.from_qualified_name)
                if from_symbol is None:
                    continue
                target = (
                    symbol_rows.get(edge.to_qualified_name)
                    if edge.to_qualified_name
                    else _resolve_callee(edge.to_name, symbol_rows)
                )
                if target is None and edge.to_qualified_name is None:
                    target = _resolve_callee(edge.to_name, symbol_rows)
                session.add(
                    CodeGraphEdge(
                        organization_id=principal.organization_id,
                        snapshot_id=snapshot.id,
                        repository_id=repository.id,
                        from_symbol_id=from_symbol.id,
                        to_symbol_id=None if target is None else target.id,
                        relation=edge.relation,
                        to_name=(edge.to_qualified_name or edge.to_name)[:1000],
                        attributes=edge.attributes,
                    )
                )
        repository.current_snapshot_id = snapshot.id
        return repository, snapshot

    async def search_symbols(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        query: str,
        repository: str | None = None,
        qualified_name: str | None = None,
        limit: int = 20,
    ) -> list[SymbolHit]:
        statement = (
            select(CodeSymbol, CodeRepository, CodeSnapshot, CodeSourceFile)
            .join(CodeRepository, CodeRepository.id == CodeSymbol.repository_id)
            .join(CodeSnapshot, CodeSnapshot.id == CodeSymbol.snapshot_id)
            .join(CodeSourceFile, CodeSourceFile.id == CodeSymbol.file_id)
            .where(
                CodeSymbol.organization_id == principal.organization_id,
                CodeRepository.organization_id == principal.organization_id,
                CodeRepository.current_snapshot_id == CodeSnapshot.id,
                _authorization_clause(principal),
            )
        )
        if repository:
            statement = statement.where(CodeRepository.name == repository)
        term = (qualified_name or query).strip()
        if not term:
            raise ValidationError("code_operation_invalid", "A symbol query is required")
        pattern = f"%{term}%"
        clauses = [
            CodeSymbol.qualified_name == term,
            CodeSymbol.name.ilike(pattern),
            CodeSymbol.qualified_name.ilike(pattern),
            CodeSourceFile.path.ilike(pattern),
        ]
        for token in list(dict.fromkeys(_SEARCH_TOKEN.findall(term)))[:12]:
            if len(token) < 2:
                continue
            token_pattern = f"%{token}%"
            clauses.extend(
                [
                    CodeSymbol.name.ilike(token_pattern),
                    CodeSymbol.qualified_name.ilike(token_pattern),
                    CodeSourceFile.path.ilike(token_pattern),
                ]
            )
        fetch_limit = min(max(limit * 5, 40), 100)
        statement = statement.where(or_(*clauses)).limit(fetch_limit)
        rows = (await session.execute(statement)).all()
        hits = [
            self._hit(symbol, repository_row, snapshot, source, ())
            for symbol, repository_row, snapshot, source in rows
        ]
        return sorted(hits, key=lambda item: _symbol_rank(item, term))[:limit]

    async def related_symbols(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        operation: str,
        symbol: str,
        repository: str | None = None,
        limit: int = 20,
    ) -> list[SymbolHit]:
        if operation not in {"code.reference", "code.callers", "code.callees"}:
            raise ValidationError(
                "code_operation_invalid",
                "The Code Graph operation is not part of the read-only contract",
            )
        origin_hits = await self.search_symbols(
            session, principal, query=symbol, repository=repository, qualified_name=symbol, limit=8
        )
        if not origin_hits:
            return []
        origin = next(
            (item for item in origin_hits if item.kind in {"FUNCTION", "METHOD", "CLASS"}),
            origin_hits[0],
        )
        relation = CodeRelation.REFERENCES if operation == "code.reference" else CodeRelation.CALLS
        if operation == "code.callees":
            statement = (
                select(CodeSymbol, CodeRepository, CodeSnapshot, CodeSourceFile, CodeGraphEdge)
                .join(CodeGraphEdge, CodeGraphEdge.to_symbol_id == CodeSymbol.id)
                .join(CodeRepository, CodeRepository.id == CodeSymbol.repository_id)
                .join(CodeSnapshot, CodeSnapshot.id == CodeSymbol.snapshot_id)
                .join(CodeSourceFile, CodeSourceFile.id == CodeSymbol.file_id)
                .where(
                    CodeGraphEdge.organization_id == principal.organization_id,
                    CodeGraphEdge.from_symbol_id == origin.symbol_id,
                    CodeGraphEdge.relation == relation,
                    CodeRepository.current_snapshot_id == CodeSnapshot.id,
                    _authorization_clause(principal),
                )
                .limit(min(limit, 100))
            )
        else:
            statement = (
                select(CodeSymbol, CodeRepository, CodeSnapshot, CodeSourceFile, CodeGraphEdge)
                .join(CodeGraphEdge, CodeGraphEdge.from_symbol_id == CodeSymbol.id)
                .join(CodeRepository, CodeRepository.id == CodeSymbol.repository_id)
                .join(CodeSnapshot, CodeSnapshot.id == CodeSymbol.snapshot_id)
                .join(CodeSourceFile, CodeSourceFile.id == CodeSymbol.file_id)
                .where(
                    CodeGraphEdge.organization_id == principal.organization_id,
                    CodeGraphEdge.to_symbol_id == origin.symbol_id,
                    CodeGraphEdge.relation == relation,
                    CodeRepository.current_snapshot_id == CodeSnapshot.id,
                    _authorization_clause(principal),
                )
                .limit(min(limit, 100))
            )
        rows = (await session.execute(statement)).all()
        hits = [
            self._hit(
                symbol_row,
                repository_row,
                snapshot,
                source,
                ({"relation": edge.relation.value, "origin": origin.qualified_name},),
            )
            for symbol_row, repository_row, snapshot, source, edge in rows
        ]
        return hits

    async def invoke(
        self, session: AsyncSession, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in _GRAPH_OPERATIONS:
            raise ValidationError(
                "code_operation_invalid",
                "The Code Graph operation is not part of the read-only contract",
            )
        repository = payload.get("repository")
        repository_name = repository if isinstance(repository, str) and repository != "*" else None
        limit = int(payload.get("limit", 20))
        if operation == "code.symbol":
            query = str(payload.get("query") or payload.get("qualified_name") or "")
            hits = await self.search_symbols(
                session,
                principal,
                query=query,
                repository=repository_name,
                qualified_name=payload.get("qualified_name")
                if isinstance(payload.get("qualified_name"), str)
                else None,
                limit=limit,
            )
        else:
            symbol = str(payload.get("symbol") or payload.get("query") or "")
            hits = await self.related_symbols(
                session,
                principal,
                operation=operation,
                symbol=symbol,
                repository=repository_name,
                limit=limit,
            )
        return {
            "operation": operation,
            "items": [item.as_dict() for item in hits],
            "count": len(hits),
        }

    async def list_repositories(
        self, session: AsyncSession, principal: Principal
    ) -> list[CodeRepository]:
        rows = list(
            await session.scalars(
                select(CodeRepository).where(
                    CodeRepository.organization_id == principal.organization_id,
                    _authorization_clause(principal),
                )
            )
        )
        return rows

    async def _writable_repository(
        self, session: AsyncSession, principal: Principal, repository_id: UUID
    ) -> CodeRepository:
        repository = await session.scalar(
            select(CodeRepository)
            .where(
                CodeRepository.id == repository_id,
                CodeRepository.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if repository is None or repository.deleted_at is not None:
            raise NotFoundError("Code repository", repository_id)
        if not _authorized(principal, repository.classification, repository.acl):
            raise AuthorizationError("code_write_denied", "The repository is not authorized")
        return repository

    async def _previous_files(
        self,
        session: AsyncSession,
        organization_id: UUID,
        previous: CodeSnapshot | None,
    ) -> dict[str, CodeSourceFile]:
        if previous is None:
            return {}
        rows = list(
            await session.scalars(
                select(CodeSourceFile).where(
                    CodeSourceFile.organization_id == organization_id,
                    CodeSourceFile.snapshot_id == previous.id,
                )
            )
        )
        return {item.path: item for item in rows}

    @staticmethod
    def _hit(
        symbol: CodeSymbol,
        repository: CodeRepository,
        snapshot: CodeSnapshot,
        source: CodeSourceFile,
        relations: tuple[dict[str, Any], ...],
    ) -> SymbolHit:
        return SymbolHit(
            repository_id=repository.id,
            repository=repository.name,
            commit_id=snapshot.commit_id,
            snapshot_id=snapshot.id,
            symbol_id=symbol.id,
            path=source.path,
            language=source.language,
            kind=symbol.kind.value,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            relations=relations,
        )


def module_path_hint(path: str) -> str:
    return module_name_for(path)
