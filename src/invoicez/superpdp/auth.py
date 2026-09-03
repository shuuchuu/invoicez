from __future__ import annotations

import json
import time
from dataclasses import asdict

import niquests
from pydantic.dataclasses import dataclass

from invoicez.superpdp.config import (
    ENDPOINT,
    load_credentials,
    token_cache_path,
)

TOKEN_ENDPOINT = "/oauth2/token"
EXPIRY_LEEWAY_SECONDS = 30


@dataclass(frozen=True)
class Token:
    access_token: str
    expires_at: float | None

    def is_valid(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() < self.expires_at - EXPIRY_LEEWAY_SECONDS


def _load_cached_token() -> Token | None:
    path = token_cache_path()
    if not path.is_file():
        return None
    try:
        return Token(**json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _store_token(token: Token) -> None:
    path = token_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(token)))
    path.chmod(0o600)


def _request_token() -> Token:
    creds = load_credentials()
    response = niquests.post(
        f"{ENDPOINT}{TOKEN_ENDPOINT}",
        data={
            "grant_type": "client_credentials",
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
        },
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    expires_in = body.get("expires_in")
    expires_at = time.time() + expires_in if expires_in is not None else None
    return Token(access_token=body["access_token"], expires_at=expires_at)


def get_access_token(*, force_refresh: bool = False) -> Token:
    """Return a valid SuperPDP access token, refreshing and caching it as needed.

    Args:
        force_refresh: Ignore any cached token and request a new one.
    """
    if not force_refresh:
        cached = _load_cached_token()
        if cached is not None and cached.is_valid():
            return cached

    token = _request_token()
    _store_token(token)
    return token
