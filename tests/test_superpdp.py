import json
from pathlib import Path
from typing import Any

import niquests
from pytest import MonkeyPatch, fixture, raises

from invoicez.cli.send import send
from invoicez.exceptions import InvoicezError
from invoicez.superpdp import invoices
from invoicez.superpdp.auth import Token

TOKEN = Token(access_token="token", expires_at=None)
PDF = Path(__file__).parent / "test_cli" / "pdf" / "2020-10-001-Company.pdf"
VALID_REPORT = {"data": [{"is_valid": True, "subreports": []}]}


def _response(status_code: int, body: Any) -> niquests.Response:
    response = niquests.Response()
    response.status_code = status_code
    response._content = (  # ruff: ignore[private-member-access]
        body if isinstance(body, bytes) else json.dumps(body).encode()
    )
    response.encoding = "utf-8"
    return response


@fixture
def replies(monkeypatch: MonkeyPatch) -> list[niquests.Response]:
    """Queue the responses that successive SuperPDP calls will return.

    Returns:
        The queue, to fill in the order the calls are made.
    """
    queue: list[niquests.Response] = []
    monkeypatch.setattr(invoices.niquests, "post", lambda *_, **__: queue.pop(0))
    monkeypatch.setattr(invoices, "get_access_token", lambda: TOKEN)
    return queue


def test_send_invoice_returns_created_invoice(
    replies: list[niquests.Response],
) -> None:
    created = {"id": 42, "company_id": 1, "created_at": "now", "direction": "out"}
    replies.append(_response(200, created))
    assert invoices.send_invoice(PDF.read_bytes()) == created


def test_send_invoice_raises_with_api_message(
    replies: list[niquests.Response],
) -> None:
    replies.append(
        _response(400, {"http_status_code": 400, "message": "invalid buyer"})
    )
    with raises(InvoicezError, match="HTTP 400: invalid buyer"):
        invoices.send_invoice(PDF.read_bytes())


def test_send_invoice_raises_without_api_message(
    replies: list[niquests.Response],
) -> None:
    replies.append(_response(401, {"http_status_code": 401}))
    with raises(InvoicezError, match=r"HTTP 401$"):
        invoices.send_invoice(PDF.read_bytes())


def test_send_invoice_raises_on_non_json_error(
    replies: list[niquests.Response],
) -> None:
    replies.append(_response(502, b"<html>Bad Gateway</html>"))
    with raises(InvoicezError, match=r"HTTP 502$"):
        invoices.send_invoice(PDF.read_bytes())


def test_send_prints_invoice_id(replies: list[niquests.Response], capsys: Any) -> None:
    replies.extend([_response(200, VALID_REPORT), _response(200, {"id": 42})])
    send(PDF, dry_run=False)
    assert "id 42" in capsys.readouterr().out


def test_send_stops_on_invalid_invoice(replies: list[niquests.Response]) -> None:
    invalid = {"data": [{"is_valid": False, "error": "bad", "subreports": []}]}
    replies.extend([_response(200, invalid), _response(200, {"id": 42})])
    with raises(InvoicezError, match="validation failed"):
        send(PDF, dry_run=False)
    assert len(replies) == 1
