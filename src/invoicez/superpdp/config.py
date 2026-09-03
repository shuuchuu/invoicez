from typing import TYPE_CHECKING

from platformdirs import user_cache_path, user_config_path
from pydantic.dataclasses import dataclass
from yaml import safe_load

from invoicez import app_name

if TYPE_CHECKING:
    from pathlib import Path

CREDS_FILENAME = "superpdp-creds.yml"
TOKEN_CACHE_FILENAME = "superpdp-token.json"

ENDPOINT = "https://api.superpdp.tech"


@dataclass(frozen=True)
class SuperPDPCredentials:
    client_id: str
    client_secret: str


def creds_path() -> "Path":
    return user_config_path(app_name) / CREDS_FILENAME


def token_cache_path() -> "Path":
    return user_cache_path(app_name) / TOKEN_CACHE_FILENAME


def load_credentials() -> SuperPDPCredentials:
    """Load SuperPDP OAuth2 client credentials from the user config directory.

    Returns:
        Credentials loaded from the standard location returned by the creds_path \
        function.

    Raises:
        FileNotFoundError: If the credentials file does not exist yet.
    """
    path = creds_path()
    if not path.is_file():
        msg = (
            f"SuperPDP credentials not found at {path}. Create it with 'client_id' and "
            "'client_secret' fields (and optionally 'endpoint')."
        )
        raise FileNotFoundError(msg)
    data = safe_load(path.read_text())
    return SuperPDPCredentials(**data)
