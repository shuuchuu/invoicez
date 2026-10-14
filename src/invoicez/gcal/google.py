"""Read-only access to Google Calendar, the only module importing Google libraries.

Needs the gcal extra: pip install invoicez[gcal].
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from platformdirs import user_config_path

from invoicez import app_name
from invoicez.exceptions import InvoicezError

CLIENT_SECRETS_FILENAME = "gcal-client-secrets.json"
TOKEN_FILENAME = "gcal-token.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]


def client_secrets_path() -> Path:
    return user_config_path(app_name) / CLIENT_SECRETS_FILENAME


def token_path() -> Path:
    return user_config_path(app_name) / TOKEN_FILENAME


def _load_token() -> Credentials | None:
    path = token_path()
    if not path.is_file():
        return None
    try:
        return Credentials.from_authorized_user_file(str(path), SCOPES)
    except ValueError:
        return None


def _store_token(creds: Credentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Created 0600 rather than chmod-ed after the write, so the refresh token
    # is never readable by others, even briefly.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(creds.to_json(), encoding="utf8")


def _login() -> Credentials:
    secrets = client_secrets_path()
    if not secrets.is_file():
        msg = (
            f"Google OAuth client secrets not found at {secrets}. Create an OAuth "
            "client of type 'Desktop app' in Google Cloud and save its JSON there."
        )
        raise InvoicezError(msg)
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    return flow.run_local_server(port=0)


def credentials() -> Credentials:
    """Return valid Google credentials, refreshing or logging in as needed.

    Logging in opens a browser for the OAuth consent screen.

    Returns:
        Credentials for the read-only calendar scopes, cached in the user
        config directory.
    """
    creds = _load_token()
    if creds is not None and creds.valid:
        return creds
    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = _login()
    else:
        creds = _login()
    _store_token(creds)
    return creds


def calendar_service(creds: Credentials) -> Any:
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _all_pages(request_page: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        result = request_page(page_token).execute()
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if page_token is None:
            return items


def list_calendars(service: Any) -> list[dict[str, Any]]:
    """List the calendars of the logged-in user.

    Returns:
        The raw calendar list entries (id, summary, primary, ...).
    """
    return _all_pages(
        lambda page_token: service.calendarList().list(pageToken=page_token)
    )


def fetch_events(
    service: Any, calendar_id: str, time_min: datetime | None = None
) -> list[dict[str, Any]]:
    """Fetch all events of a calendar, recurring ones expanded into instances.

    Args:
        service: A calendar service, from calendar_service.
        calendar_id: The calendar to read.
        time_min: Only fetch events ending after this time. Must be aware.

    Returns:
        The raw API events, ordered by start time.

    Raises:
        ValueError: If time_min is a naive datetime.
    """
    if time_min is not None and time_min.utcoffset() is None:
        msg = "time_min must be timezone-aware"
        raise ValueError(msg)
    parameters: dict[str, Any] = {
        "calendarId": calendar_id,
        "singleEvents": True,
        "orderBy": "startTime",
        "showDeleted": False,
        "maxResults": 2500,
    }
    if time_min is not None:
        parameters["timeMin"] = time_min.isoformat()
    return _all_pages(
        lambda page_token: service.events().list(**parameters, pageToken=page_token)
    )
