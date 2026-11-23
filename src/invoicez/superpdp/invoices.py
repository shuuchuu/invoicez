import re
from typing import Any

import niquests
from facturx import get_xml_from_pdf

from invoicez.exceptions import InvoicezError
from invoicez.superpdp.auth import Token, get_access_token
from invoicez.superpdp.config import ENDPOINT
from invoicez.superpdp.errors import with_request_id
from invoicez.superpdp.http import request
from invoicez.superpdp.statuses import PAID

API_BASE = f"{ENDPOINT}/v1.beta"

TIMEOUT_SECONDS = 30
# For requests creating something: giving up on them leaves their outcome unknown.
# SuperPDP checks the recipient of an invoice in the Peppol directory before answering,
# and has been seen taking a minute.
CREATE_TIMEOUT_SECONDS = 120

# How SuperPDP refuses an invoice it already has: there is no code for it.
_DUPLICATE_MESSAGE = re.compile(r"déjà existante \(id (\d+)\)")


class OutcomeUnknownError(InvoicezError):
    """SuperPDP got a request creating something, and may have carried it out."""


class DuplicateInvoiceError(InvoicezError):
    """SuperPDP refused an invoice, having one of the same seller, number and date."""

    def __init__(self, message: str, invoice_id: int):
        super().__init__(message)
        self.invoice_id = invoice_id


def _auth_headers(token: Token) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.access_token}"}


def _json_or_raise(response: niquests.Response, action: str) -> dict:
    """Return the JSON body of a successful response, or raise with the API message.

    SuperPDP only puts ``http_status_code`` and ``message`` in error bodies, and may
    omit ``message`` (a 401 body is just ``{"http_status_code": 401}``), so the HTTP
    status of the response is the source of truth.

    Args:
        response: Response returned by the SuperPDP API.
        action: What the request was doing, used in the error message.

    Returns:
        Decoded JSON body of the response.

    Raises:
        InvoicezError: If the response is an error or its body is not JSON.
    """
    try:
        body = response.json()
    except niquests.JSONDecodeError:
        body = None
    if not response.ok:
        msg = f"{action} failed with HTTP {response.status_code}"
        if isinstance(body, dict) and body.get("message"):
            msg += f": {body['message']}"
        raise InvoicezError(with_request_id(msg, response))
    if not isinstance(body, dict):
        msg = f"{action} returned an unexpected body: {response.text!r}"
        raise InvoicezError(with_request_id(msg, response))
    return body


def validate_invoice(pdf_bytes: bytes) -> dict:
    """Validate an PDF/A-3 invoice against the SuperPDP validation service.

    Args:
        pdf_bytes: PDF/A-3 bytes of the invoice to validate.

    Returns:
        Response dictionary from the API endpoint.

    Raises:
        InvoicezError: If SuperPDP cannot be reached or answers with an error.
    """
    action = "Invoice validation"
    try:
        response = request(
            "POST",
            f"{API_BASE}/validation_reports",
            files={"file": ("invoice.pdf", pdf_bytes)},
            timeout=TIMEOUT_SECONDS,
        )
    except niquests.RequestException as error:
        raise InvoicezError(_network_message(action, error)) from error
    return _json_or_raise(response, action)


def _network_message(action: str, error: niquests.RequestException) -> str:
    return f"{action} failed: {error}"


def _call(
    method: str, path: str, *, timeout: float = TIMEOUT_SECONDS, **kwargs: Any
) -> niquests.Response:
    """Make an authenticated request, logging in again once if the token is refused.

    Network errors of the request itself are left as raised by niquests, unlike those
    of the login.

    Args:
        method: HTTP method.
        path: Path of the endpoint, under the API base.
        timeout: How long to wait for the response, in seconds.
        **kwargs: Passed to niquests.

    Returns:
        The response, whatever its status.
    """

    def call(token: Token) -> niquests.Response:
        return request(
            method,
            f"{API_BASE}{path}",
            headers=_auth_headers(token),
            timeout=timeout,
            **kwargs,
        )

    response = call(get_access_token())
    # A cached token can be revoked before it expires; nothing was done with it.
    if response.status_code == 401:
        response = call(get_access_token(force_refresh=True))
    return response


def _request(method: str, path: str, action: str, **kwargs: Any) -> dict:
    """Make an authenticated request to an endpoint answering in JSON.

    Args:
        method: HTTP method.
        path: Path of the endpoint, under the API base.
        action: What the request is doing, used in error messages.
        **kwargs: Passed to niquests.

    Returns:
        Decoded JSON body of the response.

    Raises:
        InvoicezError: If SuperPDP cannot be reached or answers with an error.
    """
    try:
        response = _call(method, path, **kwargs)
    except niquests.RequestException as error:
        raise InvoicezError(_network_message(action, error)) from error
    return _json_or_raise(response, action)


def _create(path: str, action: str, **kwargs: Any) -> dict:
    """Make an authenticated request creating something.

    Args:
        path: Path of the endpoint, under the API base.
        action: What the request is doing, used in error messages.
        **kwargs: Passed to niquests.

    Returns:
        What was created, with its id.

    Raises:
        OutcomeUnknownError: If it may have been created: no answer came back, or one
            that does not tell.
        InvoicezError: If it was not created.
    """
    try:
        response = _call("POST", path, timeout=CREATE_TIMEOUT_SECONDS, **kwargs)
    except niquests.ConnectTimeout as error:
        raise InvoicezError(_network_message(action, error)) from error
    except niquests.RequestException as error:
        raise OutcomeUnknownError(_network_message(action, error)) from error
    try:
        created = _json_or_raise(response, action)
    except InvoicezError as error:
        # SuperPDP, or a proxy before it, may fail after creating it.
        if response.status_code is not None and 400 <= response.status_code < 500:
            raise
        raise OutcomeUnknownError(error.message) from error
    if "id" not in created:
        msg = f"{action} returned no id: {response.text!r}"
        raise OutcomeUnknownError(with_request_id(msg, response))
    return created


def list_sent_invoices() -> list[dict]:
    """Return every invoice sent through SuperPDP, with its number and events.

    Returns:
        The invoices, by increasing id.
    """
    params: dict[str, str | list[str]] = {
        "direction": "out",
        "expand[]": ["en_invoice", "events"],
        "limit": "1000",
    }
    invoices: list[dict] = []
    while True:
        page = _request("GET", "/invoices", "Listing sent invoices", params=params)
        invoices.extend(page["data"])
        if not page["has_after"] or not page["data"]:
            return invoices
        params["starting_after_id"] = str(page["data"][-1]["id"])


def invoice_number(invoice: dict) -> str | None:
    return (invoice.get("en_invoice") or {}).get("number")


def get_invoice(invoice_id: int) -> dict:
    """Return an invoice sent through SuperPDP, with its events.

    Args:
        invoice_id: SuperPDP id of the invoice.

    Returns:
        The invoice.
    """
    return _request("GET", f"/invoices/{invoice_id}", "Fetching the invoice")


def get_original_invoice(invoice_id: int) -> bytes:
    """Return the file an invoice was sent as, unmodified.

    Args:
        invoice_id: SuperPDP id of the invoice.

    Returns:
        The file: XML for the invoices sent by send_invoice.

    Raises:
        InvoicezError: If SuperPDP cannot be reached or answers with an error.
    """
    action = "Fetching the invoice as sent"
    try:
        response = _call(
            "GET", f"/invoices/{invoice_id}", params={"format": "original"}
        )
    except niquests.RequestException as error:
        raise InvoicezError(_network_message(action, error)) from error
    if not response.ok:
        # Raises, with the message of SuperPDP.
        _json_or_raise(response, action)
    return response.content or b""


def mark_paid(invoice_id: int) -> dict:
    """Report a sent invoice as paid in full (fr:212, Encaissée), as of now.

    The seller reports this status: the buyer's platform does not. Without
    details, SuperPDP reports the total of the invoice as received.

    Args:
        invoice_id: SuperPDP id of the invoice.

    Returns:
        The event created, queued by SuperPDP to be sent on.
    """
    return _create(
        "/invoice_events",
        "Marking the invoice as paid",
        json={"invoice_id": invoice_id, "status_code": PAID},
    )


def send_invoice(pdf_bytes: bytes, *, external_id: str | None = None) -> dict:
    """Send an PDF/A-3 invoice document to SuperPDP as the authenticated seller.

    Args:
        pdf_bytes: PDF/A-3 bytes of the invoice to validate.
        external_id: Identifier of the invoice on our side, kept by SuperPDP.

    Returns:
        The created invoice, as returned by the API endpoint.

    Raises:
        DuplicateInvoiceError: If SuperPDP already has it.
        OutcomeUnknownError: If it may have been sent.
        InvoicezError: If it was not sent.
    """
    _, xml_bytes = get_xml_from_pdf(pdf_bytes)
    try:
        return _create(
            "/invoices",
            "Invoice sending",
            params={"external_id": external_id} if external_id else None,
            data=xml_bytes,
        )
    except OutcomeUnknownError:
        raise
    except InvoicezError as error:
        match = _DUPLICATE_MESSAGE.search(error.message)
        if match is None:
            raise
        raise DuplicateInvoiceError(error.message, int(match[1])) from error
