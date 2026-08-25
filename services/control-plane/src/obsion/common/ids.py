from uuid import UUID

from uuid6 import uuid7


def new_id() -> UUID:
    """Return a time-sortable UUID without exposing persistence details."""

    return uuid7()
