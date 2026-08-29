import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from obsion.common.errors import ValidationError

_BLOCKED_FUNCTIONS = {
    "dblink",
    "dblink_connect",
    "lo_export",
    "lo_import",
    "pg_read_binary_file",
    "pg_read_file",
    "pg_sleep",
    "pg_terminate_backend",
    "pg_cancel_backend",
}
_ALLOWED_FUNCTIONS = {
    "abs",
    "avg",
    "ceil",
    "coalesce",
    "count",
    "date_add",
    "date_diff",
    "date_format",
    "date_part",
    "date_sub",
    "floor",
    "length",
    "lower",
    "max",
    "min",
    "nullif",
    "regexp_replace",
    "round",
    "split_part",
    "sum",
    "substring",
    "timestamp_trunc",
    "to_char",
    "trim",
    "upper",
}
_NON_FUNCTION_EXPRESSIONS = {
    "and",
    "between",
    "eq",
    "exists",
    "gt",
    "gte",
    "ilike",
    "in",
    "is",
    "like",
    "lt",
    "lte",
    "neq",
    "not",
    "or",
}


@dataclass(frozen=True, slots=True)
class SqlValidationResult:
    valid: bool
    normalized_sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    applied_limit: int
    warnings: tuple[str, ...] = ()
    statement_type: str = "SELECT"
    estimated_scan_cost: int = 0


def _blocked_types() -> tuple[type[exp.Expression], ...]:
    names = [
        "Alter",
        "Command",
        "Copy",
        "Create",
        "Delete",
        "Drop",
        "Execute",
        "Grant",
        "Insert",
        "LoadData",
        "Lock",
        "Merge",
        "Pragma",
        "Revoke",
        "Set",
        "Transaction",
        "TruncateTable",
        "Update",
        "Use",
    ]
    return tuple(value for name in names if (value := getattr(exp, name, None)) is not None)


def _table_name(table: exp.Table) -> str:
    parts = [table.catalog, table.db, table.name]
    return ".".join(part for part in parts if part).lower()


class SqlPolicyValidator:
    def __init__(self, *, default_limit: int = 500, max_limit: int = 5000) -> None:
        self.default_limit = default_limit
        self.max_limit = max_limit

    def validate(
        self,
        sql: str,
        *,
        dialect: str = "postgres",
        allowed_tables: set[str] | None = None,
        allowed_columns: set[str] | None = None,
        require_limit: bool = False,
        scan_budget: int | None = None,
    ) -> SqlValidationResult:
        statement_type, explain_prefix, sql_to_parse = _prepare_sql(sql)
        try:
            expressions = sqlglot.parse(sql_to_parse, read=dialect)
        except sqlglot.errors.ParseError as exc:
            raise ValidationError("sql_parse_failed", "SQL could not be parsed") from exc
        if len(expressions) != 1:
            raise ValidationError("sql_multiple_statements", "Exactly one SQL statement is allowed")
        expression = expressions[0]
        if expression is None or not isinstance(expression, exp.Query):
            raise ValidationError(
                "sql_read_only_required", "Only read-only query statements are allowed"
            )
        if any(expression.find(node_type) is not None for node_type in _blocked_types()):
            raise ValidationError("sql_mutation_denied", "SQL contains a prohibited operation")
        if expression.args.get("into") is not None:
            raise ValidationError("sql_select_into_denied", "SELECT INTO is not allowed")

        functions = set()
        for function in expression.find_all(exp.Func):
            name = function.sql_name().lower()  # type: ignore[no-untyped-call]
            if isinstance(function, exp.Anonymous):
                name = function.name.lower()
            if name in _NON_FUNCTION_EXPRESSIONS:
                continue
            functions.add(name)
        prohibited = sorted(functions.intersection(_BLOCKED_FUNCTIONS))
        unknown = sorted(functions - _ALLOWED_FUNCTIONS - _BLOCKED_FUNCTIONS)
        if prohibited or unknown:
            raise ValidationError(
                "sql_function_denied",
                "SQL uses a prohibited or unregistered function",
                functions=sorted(set(prohibited) | set(unknown)),
            )

        tables = tuple(sorted({_table_name(table) for table in expression.find_all(exp.Table)}))
        if not tables:
            raise ValidationError(
                "sql_table_required", "The query must reference an authorized table"
            )
        if allowed_tables is not None:
            normalized_allowed = {name.lower() for name in allowed_tables}
            denied_tables = sorted(set(tables) - normalized_allowed)
            if denied_tables:
                raise ValidationError(
                    "sql_table_denied",
                    "SQL references a table outside the allowed scope",
                    tables=denied_tables,
                )

        columns = tuple(
            sorted(
                {
                    ".".join(part for part in [column.table, column.name] if part).lower()
                    for column in expression.find_all(exp.Column)
                    if column.name != "*"
                }
            )
        )
        if allowed_columns is not None:
            normalized_columns = {name.lower() for name in allowed_columns}
            denied_columns = sorted(
                column
                for column in columns
                if column not in normalized_columns
                and column.split(".")[-1] not in normalized_columns
            )
            wildcard_projection = any(
                isinstance(node.parent, (exp.Select, exp.Column))
                for node in expression.find_all(exp.Star)
            )
            if denied_columns or wildcard_projection:
                raise ValidationError(
                    "sql_column_denied",
                    "SQL references a column outside the allowed scope",
                    columns=[*denied_columns, "*"] if wildcard_projection else denied_columns,
                )

        applied_limit, warnings = self._apply_limit(expression, require_limit=require_limit)
        estimated_scan_cost = _estimate_scan_cost(expression, tables=tables, columns=columns)
        if scan_budget is not None:
            if isinstance(scan_budget, bool) or not isinstance(scan_budget, int) or scan_budget < 1:
                raise ValidationError("sql_scan_budget_invalid", "SQL scan budget must be positive")
            if estimated_scan_cost > scan_budget:
                raise ValidationError(
                    "sql_scan_budget_exceeded",
                    "The query exceeds the configured scan budget",
                    estimated_scan_cost=estimated_scan_cost,
                    scan_budget=scan_budget,
                )
        normalized = expression.sql(dialect=dialect, pretty=True)
        if statement_type == "EXPLAIN":
            normalized = f"{explain_prefix}{normalized}"
        return SqlValidationResult(
            valid=True,
            normalized_sql=normalized,
            tables=tables,
            columns=columns,
            applied_limit=applied_limit,
            warnings=tuple(warnings),
            statement_type=statement_type,
            estimated_scan_cost=estimated_scan_cost,
        )

    def _apply_limit(
        self, expression: exp.Expression, *, require_limit: bool = False
    ) -> tuple[int, list[str]]:
        warnings: list[str] = []
        limit_expression = expression.args.get("limit")
        requested: int | None = None
        if isinstance(limit_expression, exp.Limit):
            raw = limit_expression.expression
            if isinstance(raw, exp.Literal) and raw.is_int:
                requested = int(raw.this)
        if requested is None:
            if require_limit:
                raise ValidationError(
                    "sql_limit_required", "A literal LIMIT is required for governed SQL"
                )
            expression.set("limit", exp.Limit(expression=exp.Literal.number(self.default_limit)))
            warnings.append("default_limit_applied")
            return self.default_limit, warnings
        applied = min(max(requested, 1), self.max_limit)
        if applied != requested:
            expression.set("limit", exp.Limit(expression=exp.Literal.number(applied)))
            warnings.append("maximum_limit_applied")
        return applied, warnings


_EXPLAIN_PREFIX = re.compile(
    r"^EXPLAIN(?:\s*\((?P<options>[^)]*)\))?\s+", re.IGNORECASE | re.DOTALL
)


def _prepare_sql(sql: str) -> tuple[str, str, str]:
    """Return the statement kind, safe EXPLAIN prefix, and query to parse.

    sqlglot currently parses PostgreSQL EXPLAIN as a generic Command. Parsing the
    underlying query separately lets the same AST policy guard SELECT/WITH while
    explicitly rejecting EXPLAIN ANALYZE (which executes the query).
    """
    stripped = sql.strip()
    if re.match(r"^EXPLAIN\s+(?:ANALYZE|BUFFERS)\b", stripped, re.IGNORECASE):
        raise ValidationError(
            "sql_explain_execution_denied",
            "EXPLAIN options that execute or inspect runtime buffers are not allowed",
        )
    match = _EXPLAIN_PREFIX.match(stripped)
    if match is None:
        return "SELECT", "", sql
    options = (match.group("options") or "").strip()
    normalized_options = " ".join(options.split()).upper()
    if any(token in normalized_options.split(",") for token in ("ANALYZE", "BUFFERS")):
        raise ValidationError(
            "sql_explain_execution_denied",
            "EXPLAIN options that execute or inspect runtime buffers are not allowed",
        )
    prefix = "EXPLAIN"
    if options:
        prefix = f"EXPLAIN ({options})"
    return "EXPLAIN", f"{prefix} ", sql.strip()[match.end() :]


def _estimate_scan_cost(
    expression: exp.Expression, *, tables: tuple[str, ...], columns: tuple[str, ...]
) -> int:
    """Compute a deterministic preflight cost when the database has no stats.

    This is deliberately conservative and monotonic: joins, projected columns,
    predicates, and nested expressions all increase the cost. PostgreSQL EXPLAIN
    remains the authoritative budget check in the query gateway when available.
    """
    node_count = sum(1 for _ in expression.walk())
    predicate_count = sum(1 for _ in expression.find_all(exp.Predicate))
    return max(1, len(tables) * 1000 + len(columns) * 100 + node_count * 10 + predicate_count * 25)
