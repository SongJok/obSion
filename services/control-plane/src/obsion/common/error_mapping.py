def application_error_code(status_code: int) -> int:
    """Map an HTTP-equivalent domain status into the stable JSON-RPC server range."""

    if status_code in {401, 403}:
        return -32003
    if status_code == 404:
        return -32004
    if status_code == 409:
        return -32009
    if status_code == 422:
        return -32022
    if status_code == 429:
        return -32029
    if status_code >= 500:
        return -32050
    return -32000
