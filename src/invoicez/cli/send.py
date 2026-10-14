from datetime import date
from pprint import pprint

from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.runner import load_document
from invoicez.runner import run as runner_run
from invoicez.superpdp.invoices import (
    find_sent_invoice,
    send_invoice,
    validate_invoice,
)


def send(
    document: str,
    template: str = "main",
    *,
    validate: bool = True,
    dry_run: bool = True,
) -> None:
    """Build an invoice and send it via SuperPDP, then archive its PDF.

    The PDF is built in sendbox/ and, once sent, moved to archive/ in a folder per
    company and year. Invoices SuperPDP already has are not sent again.

    Args:
        document: YAML file of the invoice, or its number (e.g. 2026-10-003).
        template: Template to use for the compilation.
        validate: Validate the invoice against SuperPDP before sending.
        dry_run: Build (and optionally validate) the invoice without sending it.

    Raises:
        InvoicezError: If the document is a quote, was already sent, is invalid, or
            SuperPDP rejects it.
    """
    paths = Paths()
    path = paths.resolve_document(document)
    data = load_document(path, paths.git_dir)
    if data.get("quote"):
        msg = f"{path.name} is a quote, only invoices are sent"
        raise InvoicezError(msg)
    number = str(data["document_number"])

    sent = find_sent_invoice(number)
    if sent is not None:
        msg = f"Invoice {number} was already sent (SuperPDP id {sent['id']})"
        raise InvoicezError(msg)

    pdf_path = runner_run(
        path,
        template,
        paths.variables,
        paths.jinja2_dir,
        paths.git_dir,
        paths.assets_dir,
        paths.build_dir,
        paths.sendbox_dir,
    )
    pdf_bytes = pdf_path.read_bytes()

    if validate:
        report = validate_invoice(pdf_bytes)["data"][0]
        if not report["is_valid"]:
            if report.get("error"):
                print(report["error"])
            pprint(report["subreports"])
            msg = "Invoice validation failed, invoice not sent"
            raise InvoicezError(msg)
        print("Invoice validation succeeded")

    relative_pdf_path = pdf_path.relative_to(paths.git_dir)
    if dry_run:
        print(
            f"Dry run: {relative_pdf_path} not sent. "
            "Pass --no-dry-run to actually send it."
        )
        return

    invoice = send_invoice(pdf_bytes, external_id=number)
    print(f"Invoice {number} sent with SuperPDP id {invoice['id']}")

    year = date.fromisoformat(str(data["date"])).year
    archive_path = paths.archive_dir / data["company"] / str(year) / pdf_path.name
    if archive_path.exists():
        msg = (
            f"{archive_path.relative_to(paths.git_dir)} already exists, "
            f"left the sent invoice in {relative_pdf_path}"
        )
        raise InvoicezError(msg)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.rename(archive_path)
    print(f"Archived to {archive_path.relative_to(paths.git_dir)}")
