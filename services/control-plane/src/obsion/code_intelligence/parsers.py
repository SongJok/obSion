"""Static source parsers. Repository files are never executed."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from obsion.common.errors import ValidationError
from obsion.domain.enums import CodeRelation, CodeSymbolKind

PARSER_VERSION = "code-graph-v1"
MAX_FILE_BYTES = 256 * 1024
_SQL_TABLE = re.compile(r"(?is)\b(?:from|join|into|update|table)\s+(?:only\s+)?([A-Za-z_][\w.]*)")
_SQL_WRITE = re.compile(r"(?is)\b(?:insert\s+into|update|delete\s+from)\b")
_JAVA_CLASS = re.compile(r"\b(?:public|protected|private)?\s*(?:static\s+)?class\s+(\w+)")
_JAVA_METHOD = re.compile(
    r"\b(?:public|protected|private)\s+(?:static\s+)?[\w.<>,\[\]]+\s+(\w+)\s*\("
)
_TS_CLASS = re.compile(r"\b(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")
_TS_FUNCTION = re.compile(r"\b(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
_TS_METHOD = re.compile(r"(?:(?:public|private|protected|async)\s+)+(\w+)\s*\(")
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
}


@dataclass(frozen=True, slots=True)
class ParsedSymbol:
    kind: CodeSymbolKind
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedEdge:
    relation: CodeRelation
    from_qualified_name: str
    to_name: str
    to_qualified_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedFile:
    path: str
    language: str
    content_hash: str
    size_bytes: int
    symbols: tuple[ParsedSymbol, ...]
    edges: tuple[ParsedEdge, ...]
    parse_error: str | None = None


def normalize_repository_path(path: str) -> str:
    candidate = path.replace("\\", "/").strip()
    if not candidate or candidate.startswith("/") or ":" in candidate[:2]:
        raise ValidationError("code_path_invalid", "Source path must be a relative POSIX path")
    posix = PurePosixPath(candidate)
    if ".." in posix.parts or posix.is_absolute() or not posix.parts:
        raise ValidationError("code_path_invalid", "Source path must not escape the repository")
    if any(part.startswith(".") and part not in {".gitignore"} for part in posix.parts[:-1]):
        raise ValidationError("code_path_invalid", "Hidden source directories are not indexed")
    return posix.as_posix()


def detect_language(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return _LANGUAGE_BY_SUFFIX.get(suffix, "text")


def parse_source_file(path: str, content: bytes) -> ParsedFile:
    normalized = normalize_repository_path(path)
    if len(content) > MAX_FILE_BYTES:
        raise ValidationError(
            "code_file_too_large",
            "A source file exceeds the Code Graph size limit",
            max_bytes=MAX_FILE_BYTES,
            path=normalized,
        )
    language = detect_language(normalized)
    digest = hashlib.sha256(content).hexdigest()
    if language == "python":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return ParsedFile(normalized, language, digest, len(content), (), (), "not utf-8")
        return _parse_python(normalized, text, digest, len(content))
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ParsedFile(normalized, language, digest, len(content), (), (), "not utf-8")
    if language in {"java", "kotlin"}:
        return _parse_java_like(normalized, text, digest, len(content), language)
    if language in {"javascript", "typescript"}:
        return _parse_javascript_like(normalized, text, digest, len(content), language)
    return ParsedFile(normalized, language, digest, len(content), (), ())


def module_name_for(path: str) -> str:
    posix = PurePosixPath(normalize_repository_path(path))
    stem = posix.with_suffix("")
    parts = [part for part in stem.parts if part not in {"src", "app", "lib", "main", "java"}]
    return ".".join(parts) or stem.name


def _parse_python(path: str, source: str, digest: str, size: int) -> ParsedFile:
    module = module_name_for(path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return ParsedFile(path, "python", digest, size, (), (), f"syntax error: {exc.msg}")
    symbols: list[ParsedSymbol] = [
        ParsedSymbol(
            CodeSymbolKind.MODULE,
            module.split(".")[-1],
            module,
            1,
            getattr(tree, "end_lineno", None) or max(source.count("\n"), 1),
        )
    ]
    edges: list[ParsedEdge] = []
    import_aliases: dict[str, str] = {}

    def add_sql(owner: str, value: str) -> None:
        if not isinstance(value, str) or not _SQL_TABLE.search(value):
            return
        writes = _SQL_WRITE.search(value) is not None
        for match in _SQL_TABLE.finditer(value):
            table = match.group(1)
            kind = CodeRelation.WRITES_TABLE if writes else CodeRelation.READS_TABLE
            table_name = table.split(".")[-1]
            table_qname = f"table.{table_name}"
            symbols.append(
                ParsedSymbol(
                    CodeSymbolKind.TABLE, table_name, table_qname, 1, 1, attributes={"sql": True}
                )
            )
            edges.append(ParsedEdge(kind, owner, table_name, table_qname, {"table": table}))

    def visit(node: ast.AST, parent: str, class_name: str | None) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.asname or alias.name.split(".")[-1]
                import_aliases[imported] = alias.name
                edges.append(
                    ParsedEdge(
                        CodeRelation.DEPENDS_ON, parent, alias.name, alias.name, {"kind": "import"}
                    )
                )
            return
        if isinstance(node, ast.ImportFrom):
            module_ref = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                target = f"{module_ref}.{alias.name}" if module_ref else alias.name
                import_aliases[local] = target
                edges.append(
                    ParsedEdge(
                        CodeRelation.DEPENDS_ON, parent, target, target, {"kind": "from-import"}
                    )
                )
            return
        if isinstance(node, ast.ClassDef):
            qname = f"{parent}.{node.name}" if parent else node.name
            symbols.append(
                ParsedSymbol(
                    CodeSymbolKind.CLASS,
                    node.name,
                    qname,
                    node.lineno,
                    node.end_lineno or node.lineno,
                )
            )
            edges.append(ParsedEdge(CodeRelation.CONTAINS, parent, node.name, qname))
            for child in node.body:
                visit(child, qname, node.name)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = CodeSymbolKind.METHOD if class_name else CodeSymbolKind.FUNCTION
            qname = f"{parent}.{node.name}"
            symbols.append(
                ParsedSymbol(
                    kind,
                    node.name,
                    qname,
                    node.lineno,
                    node.end_lineno or node.lineno,
                    signature=node.name,
                )
            )
            edges.append(ParsedEdge(CodeRelation.CONTAINS, parent, node.name, qname))
            route = _http_route(node)
            if route is not None:
                method, route_path = route
                api_name = f"{method} {route_path}"
                api_qname = f"api.{method}.{route_path.strip('/') or 'root'}"
                symbols.append(
                    ParsedSymbol(
                        CodeSymbolKind.API,
                        api_name,
                        api_qname,
                        node.lineno,
                        node.end_lineno or node.lineno,
                        attributes={"method": method, "path": route_path},
                    )
                )
                edges.append(
                    ParsedEdge(
                        CodeRelation.EXPOSES_API, qname, api_name, api_qname, {"method": method}
                    )
                )
            for descendant in ast.walk(node):
                if descendant is node:
                    continue
                if isinstance(descendant, ast.Call):
                    callee = _call_name(descendant.func)
                    if callee:
                        edges.append(
                            ParsedEdge(CodeRelation.CALLS, qname, callee, None, {"raw": callee})
                        )
                        edges.append(
                            ParsedEdge(
                                CodeRelation.REFERENCES, qname, callee, None, {"raw": callee}
                            )
                        )
                if isinstance(descendant, ast.Constant) and isinstance(descendant.value, str):
                    add_sql(qname, descendant.value)
            return
        if isinstance(node, ast.Module):
            for child in node.body:
                visit(child, parent, class_name)

    visit(tree, module, None)
    for edge in list(edges):
        if edge.to_qualified_name is None and edge.to_name in import_aliases:
            edges.append(
                ParsedEdge(
                    edge.relation,
                    edge.from_qualified_name,
                    import_aliases[edge.to_name],
                    import_aliases[edge.to_name],
                    edge.attributes,
                )
            )
    return ParsedFile(path, "python", digest, size, tuple(_unique_symbols(symbols)), tuple(edges))


def _http_route(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str] | None:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        method = decorator.func.attr.lower()
        if method not in _HTTP_METHODS:
            continue
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            value = decorator.args[0].value
            if isinstance(value, str) and value.startswith("/"):
                return method.upper(), value
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _parse_java_like(path: str, source: str, digest: str, size: int, language: str) -> ParsedFile:
    module = module_name_for(path)
    symbols: list[ParsedSymbol] = [
        ParsedSymbol(
            CodeSymbolKind.MODULE, module.split(".")[-1], module, 1, source.count("\n") + 1
        )
    ]
    edges: list[ParsedEdge] = []
    for match in _JAVA_CLASS.finditer(source):
        qname = f"{module}.{match.group(1)}"
        line = source.count("\n", 0, match.start()) + 1
        symbols.append(ParsedSymbol(CodeSymbolKind.CLASS, match.group(1), qname, line, line))
        edges.append(ParsedEdge(CodeRelation.CONTAINS, module, match.group(1), qname))
        class_qname = qname
        for method in _JAVA_METHOD.finditer(source[match.start() : match.start() + 4000]):
            if method.group(1) in {"if", "for", "while", "switch", "catch"}:
                continue
            method_qname = f"{class_qname}.{method.group(1)}"
            method_line = line + source[match.start() : match.start() + method.start()].count("\n")
            symbols.append(
                ParsedSymbol(
                    CodeSymbolKind.METHOD, method.group(1), method_qname, method_line, method_line
                )
            )
            edges.append(
                ParsedEdge(CodeRelation.CONTAINS, class_qname, method.group(1), method_qname)
            )
    return ParsedFile(path, language, digest, size, tuple(_unique_symbols(symbols)), tuple(edges))


def _parse_javascript_like(
    path: str, source: str, digest: str, size: int, language: str
) -> ParsedFile:
    module = module_name_for(path)
    symbols: list[ParsedSymbol] = [
        ParsedSymbol(
            CodeSymbolKind.MODULE, module.split(".")[-1], module, 1, source.count("\n") + 1
        )
    ]
    edges: list[ParsedEdge] = []
    for match in _TS_CLASS.finditer(source):
        qname = f"{module}.{match.group(1)}"
        line = source.count("\n", 0, match.start()) + 1
        symbols.append(ParsedSymbol(CodeSymbolKind.CLASS, match.group(1), qname, line, line))
        edges.append(ParsedEdge(CodeRelation.CONTAINS, module, match.group(1), qname))
    for match in _TS_FUNCTION.finditer(source):
        qname = f"{module}.{match.group(1)}"
        line = source.count("\n", 0, match.start()) + 1
        symbols.append(ParsedSymbol(CodeSymbolKind.FUNCTION, match.group(1), qname, line, line))
        edges.append(ParsedEdge(CodeRelation.CONTAINS, module, match.group(1), qname))
    return ParsedFile(path, language, digest, size, tuple(_unique_symbols(symbols)), tuple(edges))


def _unique_symbols(symbols: list[ParsedSymbol]) -> list[ParsedSymbol]:
    seen: dict[str, ParsedSymbol] = {}
    for symbol in symbols:
        seen.setdefault(symbol.qualified_name, symbol)
    return list(seen.values())
