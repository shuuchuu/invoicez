from rich.console import Console

from invoicez.paths import Paths
from invoicez.sending import send_document


def send(
    document: str,
    template: str = "main",
    *,
    validate: bool = True,
    yes: bool = False,
) -> None:
    """Build an invoice and send it via SuperPDP once confirmed, then archive its PDF.

    The PDF is built in sendbox/ and, once sent, moved to archive/ in a folder per
    company and year. Invoices SuperPDP already has are not sent again.

    Args:
        document: YAML file of the invoice, or its number (e.g. 2026-10-003).
        template: Template to use for the compilation.
        validate: Validate the invoice against SuperPDP before sending.
        yes: Send without asking for confirmation.
    """
    paths = Paths()
    send_document(
        Console(),
        paths,
        paths.resolve_document(document),
        template=template,
        validate=validate,
        yes=yes,
    )
