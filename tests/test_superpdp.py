import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import niquests
from pytest import MonkeyPatch, fixture, raises

from invoicez.exceptions import InvoicezError
from invoicez.superpdp import invoices
from invoicez.superpdp.auth import Token
from invoicez.superpdp.statuses import (
    describe,
    latest_event,
    problems,
    undelivered_since,
    was_transmitted,
)

PDF = Path(__file__).parent / "test_cli" / "pdf" / "2020-10-001-Company.pdf"


def _response(status_code: int, body: Any) -> niquests.Response:
    response = niquests.Response()
    response.status_code = status_code
    response._content = (  # ruff: ignore[private-member-access]
        body if isinstance(body, bytes) else json.dumps(body).encode()
    )
    response.encoding = "utf-8"
    return response


@dataclass
class _Api:
    # Responses the next requests get, in order.
    replies: list[niquests.Response] = field(default_factory=list)
    # Keyword arguments of the requests made, in order.
    calls: list[dict[str, Any]] = field(default_factory=list)
    # Access tokens handed out, by force_refresh value.
    tokens: list[bool] = field(default_factory=list)


@fixture
def api(monkeypatch: MonkeyPatch) -> _Api:
    """Stand in for the SuperPDP API.

    Returns:
        The fake, to queue replies on and read the requests made from.
    """
    fake = _Api()

    def reply(*_: Any, **kwargs: Any) -> niquests.Response:
        fake.calls.append(kwargs)
        return fake.replies.pop(0)

    def get_access_token(*, force_refresh: bool = False) -> Token:
        fake.tokens.append(force_refresh)
        return Token(access_token=f"token{len(fake.tokens)}", expires_at=None)

    monkeypatch.setattr(invoices.niquests, "post", reply)
    monkeypatch.setattr(invoices.niquests, "request", reply)
    monkeypatch.setattr(invoices, "get_access_token", get_access_token)
    return fake


def test_send_invoice_returns_created_invoice(api: _Api) -> None:
    created = {"id": 42, "company_id": 1, "created_at": "now", "direction": "out"}
    api.replies.append(_response(200, created))
    assert invoices.send_invoice(PDF.read_bytes()) == created


def test_send_invoice_raises_with_api_message(api: _Api) -> None:
    api.replies.append(
        _response(400, {"http_status_code": 400, "message": "invalid buyer"})
    )
    with raises(InvoicezError, match="HTTP 400: invalid buyer"):
        invoices.send_invoice(PDF.read_bytes())


def test_send_invoice_raises_on_non_json_error(api: _Api) -> None:
    api.replies.append(_response(502, b"<html>Bad Gateway</html>"))
    with raises(InvoicezError, match=r"HTTP 502$"):
        invoices.send_invoice(PDF.read_bytes())


def test_send_invoice_passes_external_id(api: _Api) -> None:
    api.replies.append(_response(200, {"id": 42}))
    invoices.send_invoice(PDF.read_bytes(), external_id="2020-10-001")
    assert api.calls[0]["params"] == {"external_id": "2020-10-001"}


def test_refused_token_is_refreshed_once(api: _Api) -> None:
    api.replies.extend(
        [
            _response(401, {"http_status_code": 401}),
            _response(200, {"id": 42}),
        ]
    )
    assert invoices.get_invoice(42) == {"id": 42}
    assert api.tokens == [False, True]
    assert api.calls[1]["headers"] == {"Authorization": "Bearer token2"}


def test_token_refused_twice_raises(api: _Api) -> None:
    api.replies.extend(
        [
            _response(401, {"http_status_code": 401}),
            _response(401, {"http_status_code": 401}),
        ]
    )
    with raises(InvoicezError, match=r"HTTP 401$"):
        invoices.get_invoice(42)


def _page(numbers: list[str], *, first_id: int, has_after: bool) -> niquests.Response:
    data = [
        {"id": first_id + i, "en_invoice": {"number": number}}
        for i, number in enumerate(numbers)
    ]
    return _response(
        200, {"data": data, "count": 3, "has_before": False, "has_after": has_after}
    )


def test_list_sent_invoices_reads_every_page(api: _Api) -> None:
    api.replies.extend(
        [
            _page(["2020-10-001", "2020-10-002"], first_id=1, has_after=True),
            _page(["2020-10-003"], first_id=3, has_after=False),
        ]
    )
    sent = invoices.list_sent_invoices()
    assert [invoices.invoice_number(invoice) for invoice in sent] == [
        "2020-10-001",
        "2020-10-002",
        "2020-10-003",
    ]
    assert api.calls[1]["params"]["starting_after_id"] == "2"


def _event(code: str, at: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": int(at.replace(":", "")),
        "created_at": f"2026-10-14T{at}Z",
        "status_code": code,
        "status_text": code.upper(),
        **extra,
    }


def test_latest_event_skips_administration_acks() -> None:
    invoice = {
        "events": [
            _event("api:uploaded", "10:00"),
            _event("fr:203", "10:05"),
            _event("ppf:flow-1-ack", "10:10"),
        ]
    }
    assert latest_event(invoice) == invoice["events"][1]
    assert problems(invoice) == []
    assert was_transmitted(invoice)


def test_administration_errors_are_problems() -> None:
    error = _event("ppf:flow-1-ack-error", "10:10")
    invoice = {"events": [_event("fr:203", "10:05"), error]}
    assert latest_event(invoice) == error
    assert problems(invoice) == [error]


def test_only_invalid_invoices_were_not_transmitted() -> None:
    assert not was_transmitted({"events": [_event("api:invalid", "10:00")]})
    assert was_transmitted({"events": [_event("fr:213", "10:00")]})
    assert was_transmitted({"events": []})


def test_describe_lists_reasons_and_notes() -> None:
    event = _event(
        "fr:213",
        "10:00",
        details=[
            {
                "reason": "DOUBLON",
                "notes": [{"contents": [{"content": "already received"}]}],
            }
        ],
    )
    assert describe(event) == "FR:213 (fr:213): reason DOUBLON; already received"


def test_undelivered_since() -> None:
    emitted = [_event("fr:200", "10:00"), _event("fr:201", "10:01")]
    assert undelivered_since({"events": emitted}) == datetime(
        2026, 10, 14, 10, 1, tzinfo=UTC
    )
    assert undelivered_since({"events": [*emitted, _event("fr:203", "10:05")]}) is None
    assert undelivered_since({"events": [_event("api:uploaded", "10:00")]}) is None
