import pytest

from obsion.common.errors import ValidationError
from obsion.data_intelligence.sql_policy import SqlPolicyValidator


@pytest.fixture
def validator() -> SqlPolicyValidator:
    return SqlPolicyValidator(default_limit=100, max_limit=500)


def test_read_query_is_normalized_and_bounded(validator: SqlPolicyValidator) -> None:
    result = validator.validate(
        "select account_id, sum(amount) as revenue from analytics.orders group by account_id",
        allowed_tables={"analytics.orders"},
        allowed_columns={"account_id", "amount"},
    )
    assert result.valid
    assert result.applied_limit == 100
    assert "LIMIT 100" in result.normalized_sql
    assert result.tables == ("analytics.orders",)
    assert "default_limit_applied" in result.warnings


def test_excessive_limit_is_reduced(validator: SqlPolicyValidator) -> None:
    result = validator.validate(
        "select account_id from analytics.orders limit 100000",
        allowed_tables={"analytics.orders"},
        allowed_columns={"account_id"},
    )
    assert result.applied_limit == 500
    assert "LIMIT 500" in result.normalized_sql


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("delete from analytics.orders", "sql_read_only_required"),
        (
            "select * from analytics.orders; select * from analytics.users",
            "sql_multiple_statements",
        ),
        ("select pg_sleep(10) from analytics.orders", "sql_function_denied"),
        ("copy analytics.orders to '/tmp/export'", "sql_read_only_required"),
        ("select account_id into leaked from analytics.orders", "sql_select_into_denied"),
    ],
)
def test_mutating_or_dangerous_sql_is_rejected(
    validator: SqlPolicyValidator, sql: str, code: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        validator.validate(sql, allowed_tables={"analytics.orders"})
    assert caught.value.code == code


def test_unknown_table_and_column_are_rejected(validator: SqlPolicyValidator) -> None:
    with pytest.raises(ValidationError) as table_error:
        validator.validate("select id from private.payroll", allowed_tables={"analytics.orders"})
    assert table_error.value.code == "sql_table_denied"

    with pytest.raises(ValidationError) as column_error:
        validator.validate(
            "select card_number from analytics.orders",
            allowed_tables={"analytics.orders"},
            allowed_columns={"account_id", "amount"},
        )
    assert column_error.value.code == "sql_column_denied"


def test_cte_cannot_hide_an_unauthorized_table(validator: SqlPolicyValidator) -> None:
    with pytest.raises(ValidationError) as caught:
        validator.validate(
            "with hidden as (select salary from private.payroll) select salary from hidden",
            allowed_tables={"analytics.orders"},
        )
    assert caught.value.code == "sql_table_denied"
