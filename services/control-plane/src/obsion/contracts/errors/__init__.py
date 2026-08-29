"""稳定的 Obsion 领域错误码合同。"""

from obsion.contracts.errors.catalog import (
    ErrorCodeDefinition,
    ErrorContractDefinitionError,
    get_error_code,
    registered_error_codes,
    validate_error_catalog,
    validate_error_code,
)

__all__ = [
    "ErrorCodeDefinition",
    "ErrorContractDefinitionError",
    "get_error_code",
    "registered_error_codes",
    "validate_error_catalog",
    "validate_error_code",
]
