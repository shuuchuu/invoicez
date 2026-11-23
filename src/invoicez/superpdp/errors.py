import niquests

REQUEST_ID_HEADER = "X-Request-ID"


def with_request_id(message: str, response: niquests.Response | None) -> str:
    """Add to an error message what SuperPDP needs to find the request in its logs.

    Args:
        message: The error message.
        response: Response of SuperPDP, if one came.

    Returns:
        The message, with the id of the request if the response has one.
    """
    request_id = None if response is None else response.headers.get(REQUEST_ID_HEADER)
    return f"{message} ({REQUEST_ID_HEADER}: {request_id})" if request_id else message
