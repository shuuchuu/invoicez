import niquests
from facturx import get_xml_from_pdf

from invoicez.superpdp.auth import Token, get_access_token
from invoicez.superpdp.config import ENDPOINT

API_BASE = f"{ENDPOINT}/v1.beta"


def _auth_headers(token: Token) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.access_token}"}


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
    response.raise_for_status()
    return response.json()


def send_invoice(pdf_bytes: bytes, *, token: Token | None = None) -> dict:
    """Send an PDF/A-3 invoice document to SuperPDP as the authenticated seller.

    Args:
        pdf_bytes: PDF/A-3 bytes of the invoice to validate.
        token: Authentication token.

    Returns:
        Response dictionary from the API endpoint.
    """
    token = token or get_access_token()
    _, xml_bytes = get_xml_from_pdf(pdf_bytes)
    response = niquests.post(
        f"{API_BASE}/invoices",
        data=xml_bytes,
        headers=_auth_headers(token),
        timeout=30,
    )
    return response.json()
