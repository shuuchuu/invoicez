import json
from pathlib import Path
from typing import Any

import niquests
from pytest import MonkeyPatch, fixture, raises

from invoicez.exceptions import InvoicezError
from invoicez.superpdp import invoices
from invoicez.superpdp.auth import Token

TOKEN = Token(access_token="token", expires_at=None)
# Keyword arguments of the requests made to SuperPDP, in order.
calls: list[dict[str, Any]] = []
PDF = Path(__file__).parent / "test_cli" / "pdf" / "2020-10-001-Company.pdf"


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

    def reply(*_: Any, **kwargs: Any) -> niquests.Response:
        calls.append(kwargs)
        return queue.pop(0)

    monkeypatch.setattr(invoices.niquests, "post", reply)
    monkeypatch.setattr(invoices.niquests, "get", reply)
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


def test_send_invoice_passes_external_id(replies: list[niquests.Response]) -> None:
    calls.clear()
    replies.append(_response(200, {"id": 42}))
    invoices.send_invoice(PDF.read_bytes(), external_id="2020-10-001")
    assert calls[0]["params"] == {"external_id": "2020-10-001"}


def _page(numbers: list[str], *, first_id: int, has_after: bool) -> niquests.Response:
    data = [
        {"id": first_id + i, "en_invoice": {"number": number}}
        for i, number in enumerate(numbers)
    ]
    return _response(
        200, {"data": data, "count": 3, "has_before": False, "has_after": has_after}
    )


def test_find_sent_invoice_reads_every_page(
    replies: list[niquests.Response],
) -> None:
    calls.clear()
    replies.extend(
        [
            _page(["2020-10-001", "2020-10-002"], first_id=1, has_after=True),
            _page(["2020-10-003"], first_id=3, has_after=False),
        ]
    )
    assert invoices.find_sent_invoice("2020-10-003") == {
        "id": 3,
        "en_invoice": {"number": "2020-10-003"},
    }
    assert calls[1]["params"]["starting_after_id"] == "2"


def test_find_sent_invoice_returns_none_when_never_sent(
    replies: list[niquests.Response],
) -> None:
    replies.append(_page(["2020-10-001"], first_id=1, has_after=False))
    assert invoices.find_sent_invoice("2020-10-002") is None
