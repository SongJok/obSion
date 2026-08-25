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


@dataclass(frozen=True, slots=True)
class SqlValidationResult:
    valid: bool
    normalized_sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    applied_limit: int
    warnings: tuple[str, ...] = ()


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
    ) -> SqlValidationResult:
        try:
            expressions = sqlglot.parse(sql, read=dialect)
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
            functions.add(name)
        prohibited = sorted(functions.intersection(_BLOCKED_FUNCTIONS))
        if prohibited:
            raise ValidationError(
                "sql_function_denied", "SQL uses a prohibited function", functions=prohibited
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
            if denied_columns:
                raise ValidationError(
                    "sql_column_denied",
                    "SQL references a column outside the allowed scope",
                    columns=denied_columns,
                )

        applied_limit, warnings = self._apply_limit(expression)
        return SqlValidationResult(
            valid=True,
            normalized_sql=expression.sql(dialect=dialect, pretty=True),
            tables=tables,
            columns=columns,
            applied_limit=applied_limit,
            warnings=tuple(warnings),
        )

    def _apply_limit(self, expression: exp.Expression) -> tuple[int, list[str]]:
        warnings: list[str] = []
        limit_expression = expression.args.get("limit")
        requested: int | None = None
        if isinstance(limit_expression, exp.Limit):
            raw = limit_expression.expression
            if isinstance(raw, exp.Literal) and raw.is_int:
                requested = int(raw.this)
        if requested is None:
            expression.set("limit", exp.Limit(expression=exp.Literal.number(self.default_limit)))
            warnings.append("default_limit_applied")
            return self.default_limit, warnings
        applied = min(max(requested, 1), self.max_limit)
        if applied != requested:
            expression.set("limit", exp.Limit(expression=exp.Literal.number(applied)))
            warnings.append("maximum_limit_applied")
        return applied, warnings
