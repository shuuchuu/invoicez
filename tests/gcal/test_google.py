from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from pytest import fixture, raises

from invoicez.exceptions import InvoicezError
from invoicez.gcal import google
from invoicez.gcal.google import credentials, fetch_events, list_calendars


class _FakeRequest:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _FakeCollection:
    """Serves items in pages of two and records the list() calls."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.calls: list[dict[str, Any]] = []

    def list(self, **parameters: Any) -> _FakeRequest:
        self.calls.append(parameters)
        start = int(parameters["pageToken"] or 0)
        result: dict[str, Any] = {"items": self._items[start : start + 2]}
        if start + 2 < len(self._items):
            result["nextPageToken"] = str(start + 2)
        return _FakeRequest(result)


class _FakeService:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.collection = _FakeCollection(items)

    def events(self) -> _FakeCollection:
        return self.collection

    def calendarList(self) -> _FakeCollection:  # ruff: ignore[invalid-function-name]
        return self.collection


_ITEMS = [{"id": f"event{i}"} for i in range(5)]


def test_fetch_events_reads_all_pages() -> None:
    service = _FakeService(_ITEMS)

    events = fetch_events(service, "calendar-id")

    assert events == _ITEMS
    assert [call["pageToken"] for call in service.collection.calls] == [None, "2", "4"]
    assert service.collection.calls[0] == {
        "calendarId": "calendar-id",
        "singleEvents": True,
        "orderBy": "startTime",
        "showDeleted": False,
        "maxResults": 2500,
        "pageToken": None,
    }


def test_fetch_events_time_min() -> None:
    service = _FakeService(_ITEMS)

    fetch_events(service, "calendar-id", datetime(2026, 10, 1, tzinfo=UTC))

    assert service.collection.calls[0]["timeMin"] == "2026-10-01T00:00:00+00:00"


def test_fetch_events_rejects_naive_time_min() -> None:
    with raises(ValueError, match="timezone-aware"):
        fetch_events(_FakeService([]), "calendar-id", datetime(2026, 10, 1))


def test_list_calendars_reads_all_pages() -> None:
    service = _FakeService(_ITEMS)

    assert list_calendars(service) == _ITEMS


@fixture
def config_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setattr(google, "user_config_path", lambda _: tmp_path)
    return tmp_path


def _credentials(token: str, expiry: datetime | None = None) -> Credentials:
    return Credentials(
        token=token,
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=google.SCOPES,
        expiry=expiry,
    )


def _write_token(config_dir: Path, creds: Credentials) -> None:
    (config_dir / google.TOKEN_FILENAME).write_text(creds.to_json(), encoding="utf8")


class _FakeFlow:
    logins = 0

    @classmethod
    def from_client_secrets_file(cls, path: str, scopes: list[str]) -> "_FakeFlow":
        assert Path(path).name == google.CLIENT_SECRETS_FILENAME
        assert scopes == google.SCOPES
        return cls()

    def run_local_server(self, port: int) -> Credentials:
        type(self).logins += 1
        return _credentials("new-token")


@fixture
def fake_flow(monkeypatch: Any) -> type[_FakeFlow]:
    _FakeFlow.logins = 0
    monkeypatch.setattr(google, "InstalledAppFlow", _FakeFlow)
    return _FakeFlow


def test_credentials_without_client_secrets(config_dir: Path) -> None:
    with raises(InvoicezError, match=google.CLIENT_SECRETS_FILENAME):
        credentials()


def test_credentials_login_stores_a_private_token(
    config_dir: Path, fake_flow: type[_FakeFlow]
) -> None:
    (config_dir / google.CLIENT_SECRETS_FILENAME).write_text("{}", encoding="utf8")
    token = config_dir / google.TOKEN_FILENAME
    token.write_text("not json", encoding="utf8")
    token.chmod(0o644)

    creds = credentials()

    assert creds.token == "new-token"
    assert fake_flow.logins == 1
    assert token.stat().st_mode & 0o777 == 0o600
    assert Credentials.from_authorized_user_file(str(token)).token == "new-token"


def test_credentials_reuses_a_valid_token(
    config_dir: Path, fake_flow: type[_FakeFlow]
) -> None:
    expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    _write_token(config_dir, _credentials("stored-token", expiry))

    assert credentials().token == "stored-token"
    assert fake_flow.logins == 0


def test_credentials_logs_in_again_when_refresh_fails(
    config_dir: Path, fake_flow: type[_FakeFlow], monkeypatch: Any
) -> None:
    (config_dir / google.CLIENT_SECRETS_FILENAME).write_text("{}", encoding="utf8")
    expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    _write_token(config_dir, _credentials("expired-token", expiry))

    def refresh(self: Credentials, request: Any) -> None:
        msg = "revoked"
        raise RefreshError(msg)

    monkeypatch.setattr(Credentials, "refresh", refresh)

    assert credentials().token == "new-token"
    assert fake_flow.logins == 1
