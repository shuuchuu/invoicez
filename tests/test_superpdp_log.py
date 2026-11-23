import json
import stat
from pathlib import Path
from typing import Any

import niquests
from pytest import MonkeyPatch, fixture, mark, raises

from invoicez.superpdp import http
from invoicez.superpdp.config import ENDPOINT, log_path

URL = f"{ENDPOINT}/v1.beta/invoices"


def _response(
    status_code: int, body: Any, request_id: str | None = None
) -> niquests.Response:
    response = niquests.Response()
    response.status_code = status_code
    response._content = (  # ruff: ignore[private-member-access]
        body if isinstance(body, bytes) else json.dumps(body).encode()
    )
    response.encoding = "utf-8"
    if request_id is not None:
        response.headers["x-request-id"] = request_id
    return response


@fixture
def replies(monkeypatch: MonkeyPatch) -> list[niquests.Response | BaseException]:
    """Stand in for the network.

    Returns:
        The responses the next requests get, or errors they raise, in order.
    """
    queue: list[niquests.Response | BaseException] = []

    def reply(*_: Any, **__: Any) -> niquests.Response:
        next_reply = queue.pop(0)
        if isinstance(next_reply, BaseException):
            raise next_reply
        return next_reply

    monkeypatch.setattr(niquests, "request", reply)
    return queue


def _send(number: str = "2026-11-001") -> None:
    http.request("POST", URL, params={"external_id": number}, data=b"<secret/>")


def _log(*args: str, capsys: Any) -> str:
    from invoicez.cli import main

    capsys.readouterr()
    with raises(SystemExit) as exit_info:
        main("log", *args)
    assert not exit_info.value.code
    return capsys.readouterr().out


def test_requests_are_logged_with_their_answer(replies: list) -> None:
    replies.append(_response(200, {"id": 42, "en_invoice": "secret"}, "a1"))
    _send()

    (exchange,) = http.read_log()
    assert exchange.request["method"] == "POST"
    assert exchange.request["path"] == "/v1.beta/invoices"
    assert exchange.request["external_id"] == "2026-11-001"
    assert exchange.request["at"].endswith("Z")
    assert exchange.answer is not None
    assert exchange.answer["status"] == 200
    assert exchange.answer["request_id"] == "a1"
    assert exchange.answer["id"] == 42
    assert not exchange.failed


def test_refused_requests_are_logged_with_the_message(replies: list) -> None:
    replies.append(_response(400, {"message": "déjà existante (id 7)"}, "a1"))
    _send()

    (exchange,) = http.read_log()
    assert exchange.answer is not None
    assert exchange.answer["message"] == "déjà existante (id 7)"
    assert exchange.failed


@mark.parametrize(
    "error,logged",
    [
        (niquests.ReadTimeout("read timed out"), "ReadTimeout: read timed out"),
        (niquests.ConnectionError("reset"), "ConnectionError: reset"),
        (KeyboardInterrupt(), "KeyboardInterrupt"),
        # May quote the header.
        (niquests.exceptions.InvalidHeader("Bearer secret"), "InvalidHeader"),
    ],
)
def test_requests_without_an_answer_are_logged(
    replies: list, error: BaseException, logged: str
) -> None:
    replies.append(error)
    with raises(type(error)):
        _send()

    (exchange,) = http.read_log()
    assert exchange.request["external_id"] == "2026-11-001"
    assert exchange.answer is not None
    assert exchange.answer["error"] == logged
    assert exchange.failed


def test_requests_are_logged_before_they_are_sent(monkeypatch: MonkeyPatch) -> None:
    logged: list[http.Exchange] = []

    def reply(*_: Any, **__: Any) -> niquests.Response:
        logged.extend(http.read_log())
        return _response(200, {"id": 42})

    monkeypatch.setattr(niquests, "request", reply)
    _send()

    (exchange,) = logged
    assert exchange.answer is None
    assert exchange.failed


def test_secrets_and_contents_are_not_logged(replies: list) -> None:
    replies.extend(
        [
            _response(200, {"access_token": "secret"}),
            _response(200, {"id": 42, "en_invoice": "secret"}),
            _response(200, {"message": "secret", "id": 1}),
            _response(502, b"<html>secret</html>"),
        ]
    )
    http.request(
        "POST", f"{ENDPOINT}/oauth2/token", data={"client_secret": "secret"}, timeout=10
    )
    http.request("POST", URL, headers={"Authorization": "secret"}, data=b"secret")
    http.request("GET", URL, params={"other": "secret"})
    http.request("POST", URL, json={"invoice_id": 42, "note": "secret"})

    assert len(http.read_log()) == 4
    assert "secret" not in log_path().read_text()
    assert stat.S_IMODE(log_path().stat().st_mode) == 0o600


def test_failing_to_log_does_not_prevent_requests(
    replies: list, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    # A file where the directory of the log should be.
    (tmp_path / "file").touch()
    monkeypatch.setattr(http, "log_path", lambda: tmp_path / "file" / "superpdp.log")
    replies.extend([_response(200, {"id": 42}), niquests.ReadTimeout("timed out")])

    assert http.request("POST", URL).json() == {"id": 42}
    with raises(niquests.ReadTimeout):
        http.request("POST", URL)
    assert http.read_log() == []


def test_the_log_is_rotated(replies: list, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(http, "MAX_LOG_BYTES", 1)
    replies.extend(_response(200, {"id": n}) for n in range(10))
    for n in range(10):
        _send(f"2026-11-{n:03}")

    names = {path.name for path in log_path().parent.glob("superpdp.log*")}
    assert names == {"superpdp.log", *(f"superpdp.log.{n}" for n in (1, 2, 3))}
    # Each file holds one line: the last four are two requests and their answers.
    numbers = [exchange.request["external_id"] for exchange in http.read_log()]
    assert numbers == ["2026-11-008", "2026-11-009"]
    assert not any(exchange.failed for exchange in http.read_log())


def test_damaged_lines_are_skipped(replies: list) -> None:
    replies.append(_response(200, {"id": 42}))
    _send()
    with log_path().open("a") as file:
        file.write('[]\n{"key": "cut sho')

    assert len(http.read_log()) == 1


def test_log_shows_what_to_tell_superpdp(replies: list, capsys: Any) -> None:
    replies.extend(
        [
            _response(200, {"id": 42}, "a1"),
            _response(200, {"data": []}, "b2"),
            _response(404, {"http_status_code": 404}, "c3"),
            niquests.ReadTimeout("read timed out"),
        ]
    )
    _send("2026-11-001")
    http.request("GET", URL)
    http.request("GET", f"{URL}/7")
    with raises(niquests.ReadTimeout):
        _send("2026-11-002")

    output = _log(capsys=capsys)
    assert "POST /v1.beta/invoices external_id=2026-11-001\n" in output
    assert "X-Request-ID: a1, id 42" in output
    # Only read, without failing.
    assert "b2" not in output
    assert "c3" in output
    assert "POST /v1.beta/invoices external_id=2026-11-002\n" in output
    assert "ReadTimeout: read timed out" in output
    assert f"3 of 3 requests, from {log_path()}" in output

    assert "b2" in _log("--all", capsys=capsys)
    output = _log("--limit", "1", capsys=capsys)
    assert "2026-11-002" in output
    assert "2026-11-001" not in output
    assert "1 of 3 requests" in output
    output = _log("B2", capsys=capsys)
    assert "GET /v1.beta/invoices\n" in output
    assert "1 of 1 requests" in output


def test_log_shows_interrupted_requests(replies: list, capsys: Any) -> None:
    replies.append(_response(200, {"id": 42}))
    _send()
    first, _ = log_path().read_text().splitlines()
    log_path().write_text(first + "\n")

    assert "no answer logged (interrupted" in _log(capsys=capsys)


def test_log_without_requests(capsys: Any) -> None:
    assert "0 of 0 requests" in _log(capsys=capsys)
