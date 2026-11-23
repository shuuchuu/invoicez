"""Make requests to SuperPDP, keeping a log of them.

The log tells SuperPDP which request to look for when something goes wrong, even when
no answer came. It holds neither credentials nor the content of invoices, except for
what the error messages of SuperPDP quote.
"""

import json
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from os import open as os_open
from pathlib import Path
from secrets import token_hex
from typing import Any

import niquests

from invoicez.superpdp.config import ENDPOINT, log_path
from invoicez.superpdp.errors import REQUEST_ID_HEADER

MAX_LOG_BYTES = 1_000_000
# How many full logs are kept, besides the current one.
OLD_LOGS = 3


@dataclass(frozen=True)
class Exchange:
    """A request of the log, with its answer.

    Both are as logged. There is no answer if the request was interrupted, such as by
    killing the process, or is still running.
    """

    request: dict[str, Any]
    answer: dict[str, Any] | None

    @property
    def failed(self) -> bool:
        if self.answer is None:
            return True
        status = self.answer.get("status")
        return not isinstance(status, int) or status >= 400


def request(method: str, url: str, **kwargs: Any) -> niquests.Response:
    """Make a request and log it, before sending it then once answered.

    Args:
        method: HTTP method.
        url: URL of the endpoint.
        **kwargs: Passed to niquests.

    Returns:
        The response, whatever its status.
    """
    key = token_hex(4)
    # Failing to log must neither prevent a request nor hide its outcome.
    with suppress(Exception):
        path = url.removeprefix(ENDPOINT)
        sent = {"method": method, "path": path, **_subject(kwargs)}
        _write({"key": key, "at": _now(), **sent})
    start = time.monotonic()
    try:
        response = niquests.request(method, url, **kwargs)
    except BaseException as error:
        with suppress(Exception):
            seconds = round(time.monotonic() - start, 1)
            answer = {"seconds": seconds, "error": _describe(error)}
            _write({"key": key, "at": _now(), **answer})
        raise
    with suppress(Exception):
        seconds = round(time.monotonic() - start, 1)
        answer = {"seconds": seconds, **_outcome(method, response)}
        _write({"key": key, "at": _now(), **answer})
    return response


def read_log() -> list[Exchange]:
    """Return the requests logged.

    Returns:
        The requests with their answers, oldest first.
    """
    requests: dict[str, dict[str, Any]] = {}
    answers: dict[str, dict[str, Any]] = {}
    for record in _read_records():
        key = str(record.get("key"))
        if "method" in record:
            requests[key] = record
        else:
            answers[key] = record
    return [Exchange(sent, answers.get(key)) for key, sent in requests.items()]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _subject(kwargs: dict[str, Any]) -> dict[str, Any]:
    # What the request is about: the rest may be secret.
    values = {**(kwargs.get("params") or {}), **(kwargs.get("json") or {})}
    names = ("external_id", "invoice_id", "status_code")
    return {name: values[name] for name in names if name in values}


def _describe(error: BaseException) -> str:
    name = type(error).__name__
    # Other errors may quote the request, with its token.
    if isinstance(error, niquests.ConnectionError | niquests.Timeout):
        return f"{name}: {error}"
    return name


def _outcome(method: str, response: niquests.Response) -> dict[str, Any]:
    outcome: dict[str, Any] = {"status": response.status_code}
    if request_id := response.headers.get(REQUEST_ID_HEADER):
        outcome["request_id"] = request_id
    if response.ok and method != "POST":
        return outcome
    try:
        body = response.json()
    except (ValueError, niquests.RequestException):
        return outcome
    if isinstance(body, dict):
        # A message only comes with errors, an id with what was created.
        name = "id" if response.ok else "message"
        if body.get(name) is not None:
            outcome[name] = body[name]
    return outcome


def _old_log_path(n: int) -> Path:
    path = log_path()
    return path.with_name(f"{path.name}.{n}") if n else path


def _write(record: dict[str, Any]) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size >= MAX_LOG_BYTES:
        for n in reversed(range(OLD_LOGS)):
            if _old_log_path(n).is_file():
                _old_log_path(n).replace(_old_log_path(n + 1))
    with open(
        path, "a", encoding="utf8", opener=lambda p, flags: os_open(p, flags, 0o600)
    ) as file:
        file.write(json.dumps(record) + "\n")


def _read_records() -> Iterator[dict[str, Any]]:
    for n in reversed(range(OLD_LOGS + 1)):
        path = _old_log_path(n)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf8", errors="replace").splitlines():
            # A line may have been cut short.
            with suppress(json.JSONDecodeError):
                record = json.loads(line)
                if isinstance(record, dict):
                    yield record
