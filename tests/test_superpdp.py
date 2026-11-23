import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import niquests
from pytest import MonkeyPatch, fixture, mark, raises

from invoicez.exceptions import InvoicezError
from invoicez.superpdp import auth, invoices
from invoicez.superpdp.auth import Token
from invoicez.superpdp.config import SuperPDPCredentials
from invoicez.superpdp.statuses import (
    describe,
    latest_event,
    problems,
    undelivered_since,
    was_transmitted,
)

PDF = Path(__file__).parent / "test_cli" / "pdf" / "2020-10-001-Company.pdf"


def _response(
    status_code: int, body: Any, request_id: str | None = None
) -> niquests.Response:
    response = niquests.Response()
    response.status_code = status_code
    if request_id is not None:
        response.headers["x-request-id"] = request_id
    response._content = (  # ruff: ignore[private-member-access]
        body if isinstance(body, bytes) else json.dumps(body).encode()
    )
    response.encoding = "utf-8"
    return response


@dataclass
class _Api:
    # Responses the next requests get, or errors they raise, in order.
    replies: list[niquests.Response | Exception] = field(default_factory=list)
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
        next_reply = fake.replies.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply

    def get_access_token(*, force_refresh: bool = False) -> Token:
        fake.tokens.append(force_refresh)
        return Token(access_token=f"token{len(fake.tokens)}", expires_at=None)

    monkeypatch.setattr(invoices.niquests, "request", reply)
    monkeypatch.setattr(invoices, "get_access_token", get_access_token)
    return fake


def test_send_invoice_returns_created_invoice(api: _Api) -> None:
    created = {"id": 42, "company_id": 1, "created_at": "now", "direction": "out"}
    api.replies.append(_response(200, created))
    assert invoices.send_invoice(PDF.read_bytes()) == created


def test_mark_paid_creates_a_payment_received_event(api: _Api) -> None:
    created = {"id": 3, "invoice_id": 42, "status_code": "fr:212"}
    api.replies.append(_response(200, created))

    assert invoices.mark_paid(42) == created
    # Without details, SuperPDP reports the invoice total as received.
    assert api.calls[0]["json"] == {"invoice_id": 42, "status_code": "fr:212"}


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


def test_errors_tell_the_request_id(api: _Api) -> None:
    api.replies.extend(
        [
            _response(400, {"http_status_code": 400, "message": "invalid buyer"}, "a1"),
            _response(502, b"<html>Bad Gateway</html>", "b2"),
            _response(200, {"created_at": "now"}, "c3"),
            _response(200, b"not JSON", "d4"),
        ]
    )
    with raises(InvoicezError, match=r"invalid buyer \(X-Request-ID: a1\)$"):
        invoices.send_invoice(PDF.read_bytes())
    for request_id in ("b2", "c3"):
        with raises(
            invoices.OutcomeUnknownError, match=rf"\(X-Request-ID: {request_id}\)$"
        ):
            invoices.send_invoice(PDF.read_bytes())
    with raises(InvoicezError, match=r"\(X-Request-ID: d4\)$"):
        invoices.get_invoice(42)


def test_send_invoice_tells_which_invoice_is_a_duplicate(api: _Api) -> None:
    message = "La facture est déjà existante (id 2294443)"
    api.replies.append(
        _response(400, {"http_status_code": 400, "message": message}, "a1")
    )
    with raises(invoices.DuplicateInvoiceError, match="déjà existante") as error:
        invoices.send_invoice(PDF.read_bytes())
    assert error.value.invoice_id == 2294443
    assert "X-Request-ID: a1" in error.value.message


@mark.parametrize(
    "status_code,message,expected",
    [
        # No id to go on with.
        (400, "La facture est déjà existante", InvoicezError),
        # Server errors do not tell what was done.
        (500, "La facture est déjà existante (id 7)", invoices.OutcomeUnknownError),
    ],
)
def test_send_invoice_does_not_guess_duplicates(
    api: _Api, status_code: int, message: str, expected: type[InvoicezError]
) -> None:
    api.replies.append(
        _response(status_code, {"http_status_code": status_code, "message": message})
    )
    with raises(InvoicezError) as error:
        invoices.send_invoice(PDF.read_bytes())
    assert type(error.value) is expected


def test_send_invoice_passes_external_id(api: _Api) -> None:
    api.replies.append(_response(200, {"id": 42}))
    invoices.send_invoice(PDF.read_bytes(), external_id="2020-10-001")
    assert api.calls[0]["params"] == {"external_id": "2020-10-001"}


def test_requests_creating_something_wait_longer(api: _Api) -> None:
    api.replies.extend([_response(200, {"id": 42}) for _ in range(3)])
    invoices.send_invoice(PDF.read_bytes())
    invoices.mark_paid(42)
    invoices.get_invoice(42)
    assert [call["timeout"] for call in api.calls] == [120, 120, 30]


@mark.parametrize(
    "reply",
    [
        niquests.ReadTimeout("read timed out"),
        niquests.ConnectionError("connection reset"),
        _response(500, {"http_status_code": 500}),
        _response(504, b"<html>Gateway Timeout</html>"),
        _response(200, b"<html>not JSON</html>"),
        _response(200, {"created_at": "now"}),
    ],
)
def test_send_invoice_tells_when_the_outcome_is_unknown(
    api: _Api, reply: niquests.Response | Exception
) -> None:
    api.replies.append(reply)
    with raises(invoices.OutcomeUnknownError, match="Invoice sending"):
        invoices.send_invoice(PDF.read_bytes())


@mark.parametrize(
    "reply",
    [
        niquests.ConnectTimeout("connect timed out"),
        _response(400, {"http_status_code": 400}),
    ],
)
def test_send_invoice_tells_when_nothing_was_sent(
    api: _Api, reply: niquests.Response | Exception
) -> None:
    api.replies.append(reply)
    with raises(InvoicezError) as error:
        invoices.send_invoice(PDF.read_bytes())
    assert not isinstance(error.value, invoices.OutcomeUnknownError)


def test_mark_paid_tells_when_the_outcome_is_unknown(api: _Api) -> None:
    api.replies.append(niquests.ReadTimeout("read timed out"))
    with raises(invoices.OutcomeUnknownError, match="Marking the invoice as paid"):
        invoices.mark_paid(42)


def test_login_failure_means_nothing_was_sent(monkeypatch: MonkeyPatch) -> None:
    def post(*_: Any, **__: Any) -> niquests.Response:
        msg = "read timed out"
        raise niquests.ReadTimeout(msg)

    monkeypatch.setattr(auth, "_load_cached_token", lambda: None)
    monkeypatch.setattr(auth.niquests, "request", post)
    monkeypatch.setattr(
        auth,
        "load_credentials",
        lambda: SuperPDPCredentials(client_id="id", client_secret="secret"),
    )
    with raises(InvoicezError, match="login failed: read timed out") as error:
        invoices.send_invoice(PDF.read_bytes())
    assert not isinstance(error.value, invoices.OutcomeUnknownError)


def test_refused_login_tells_the_request_id(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_load_cached_token", lambda: None)
    monkeypatch.setattr(
        auth.niquests,
        "request",
        lambda *_, **__: _response(401, {"error": "invalid_client"}, "a1"),
    )
    monkeypatch.setattr(
        auth,
        "load_credentials",
        lambda: SuperPDPCredentials(client_id="id", client_secret="secret"),
    )
    with raises(InvoicezError, match=r"login failed: .*\(X-Request-ID: a1\)$"):
        auth.get_access_token()


def test_network_errors_name_the_action(api: _Api) -> None:
    api.replies.extend([niquests.ReadTimeout("read timed out") for _ in range(3)])
    with raises(InvoicezError, match="Listing sent invoices failed: read timed out"):
        invoices.list_sent_invoices()
    with raises(InvoicezError, match="Invoice validation failed: read timed out"):
        invoices.validate_invoice(b"")
    with raises(InvoicezError, match="Fetching the invoice as sent failed: read"):
        invoices.get_original_invoice(42)


def test_get_original_invoice_returns_the_file(api: _Api) -> None:
    api.replies.extend(
        [
            _response(200, b"<xml/>"),
            _response(400, {"http_status_code": 400, "message": "unknown invoice"}),
        ]
    )
    assert invoices.get_original_invoice(42) == b"<xml/>"
    assert api.calls[0]["params"] == {"format": "original"}
    with raises(InvoicezError, match="HTTP 400: unknown invoice"):
        invoices.get_original_invoice(42)


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
