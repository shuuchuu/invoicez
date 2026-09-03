from datetime import datetime

from invoicez.superpdp.auth import get_access_token


def login(*, force: bool = False) -> None:
    """Authenticate to the SuperPDP API and cache the access token.

    Args:
        force: Ignore any cached token and request a new one.
    """
    token = get_access_token(force_refresh=force)
    print("Authenticated to SuperPDP.")
    if token.expires_at is not None:
        expiry = datetime.fromtimestamp(token.expires_at).isoformat(timespec="seconds")
        print(f"Token valid until {expiry}.")
    else:
        print("Token has no known expiry; it will be refreshed on next use.")
