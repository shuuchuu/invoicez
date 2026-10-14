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


def send_invoice(pdf_bytes: bytes, *, token: Token | None = None) -> dict:
    """Send an PDF/A-3 invoice document to SuperPDP as the authenticated seller.

    Args:
        pdf_bytes: PDF/A-3 bytes of the invoice to validate.
        token: Authentication token.

    Returns:
        The created invoice, as returned by the API endpoint.
    """
    token = token or get_access_token()
    _, xml_bytes = get_xml_from_pdf(pdf_bytes)
    response = niquests.post(
        f"{API_BASE}/invoices",
        data=xml_bytes,
        headers=_auth_headers(token),
        timeout=30,
    )
    return _json_or_raise(response, "Invoice sending")
