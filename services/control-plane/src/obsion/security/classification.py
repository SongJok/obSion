from obsion.domain.enums import Classification

_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


def maximum_classification(*values: Classification | None) -> Classification:
    return max(
        (value for value in values if value is not None),
        key=_RANK.__getitem__,
        default=Classification.PUBLIC,
    )
