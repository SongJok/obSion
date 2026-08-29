from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from obsion.contracts.errors import validate_error_code


class ErrorCodeType(TypeDecorator[str]):
    """在数据库绑定与读取边界执行中央错误码目录校验。"""

    impl = String
    cache_ok = True

    def __init__(self, length: int) -> None:
        self.column_length = length
        super().__init__(length=length)

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return validate_error_code(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return validate_error_code(value)
