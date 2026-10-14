from typing import Any

import niquests
from facturx import get_xml_from_pdf

from invoicez.exceptions import InvoicezError
from invoicez.superpdp.auth import Token, get_access_token
from invoicez.superpdp.config import ENDPOINT

API_BASE = f"{ENDPOINT}/v1.beta"


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
        raise InvoicezError(msg)
    if not isinstance(body, dict):
        msg = f"{action} returned an unexpected body: {response.text!r}"
        raise InvoicezError(msg)
    return body


def validate_invoice(pdf_bytes: bytes) -> dict:
    """Validate an PDF/A-3 invoice against the SuperPDP validation service.

    Args:
        pdf_bytes: PDF/A-3 bytes of the invoice to validate.

    Returns:
        Response dictionary from the API endpoint.
    """
    response = niquests.post(
        f"{API_BASE}/validation_reports",
        files={"file": ("invoice.pdf", pdf_bytes)},
        timeout=30,
    )
    return _json_or_raise(response, "Invoice validation")


def _request(method: str, path: str, action: str, **kwargs: Any) -> dict:
    """Make an authenticated request, logging in again once if the token is refused.

    Args:
        method: HTTP method.
        path: Path of the endpoint, under the API base.
        action: What the request is doing, used in error messages.
        **kwargs: Passed to niquests.

    Returns:
        Decoded JSON body of the response.
    """

    def call(token: Token) -> niquests.Response:
        return niquests.request(
            method,
            f"{API_BASE}{path}",
            headers=_auth_headers(token),
            timeout=30,
            **kwargs,
        )

    response = call(get_access_token())
    # A cached token can be revoked before it expires; nothing was done with it.
    if response.status_code == 401:
        response = call(get_access_token(force_refresh=True))
    return _json_or_raise(response, action)


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


def send_invoice(pdf_bytes: bytes, *, external_id: str | None = None) -> dict:
    """Send an PDF/A-3 invoice document to SuperPDP as the authenticated seller.

    Args:
        pdf_bytes: PDF/A-3 bytes of the invoice to validate.
        external_id: Identifier of the invoice on our side, kept by SuperPDP.

    Returns:
        The created invoice, as returned by the API endpoint.
    """
    _, xml_bytes = get_xml_from_pdf(pdf_bytes)
    return _request(
        "POST",
        "/invoices",
        "Invoice sending",
        params={"external_id": external_id} if external_id else None,
        data=xml_bytes,
    )
