from typing import Any

from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.sending import confirm
from invoicez.superpdp.invoices import invoice_number, list_sent_invoices, mark_paid
from invoicez.superpdp.statuses import (
    CANCELLED,
    PAID,
    describe,
    has_status,
    was_transmitted,
)


def _check(number: str, content: dict[str, Any], remote: dict | None) -> dict:
    # The invoice as sent, if it can be reported as paid.
    if "cancels" in content:
        msg = f"{number} is a credit note: only invoices are paid"
        raise InvoicezError(msg)
    if remote is None or not was_transmitted(remote):
        msg = f"{number} was not sent through SuperPDP"
        raise InvoicezError(msg)
    if has_status(remote, CANCELLED):
        msg = f"{number} is cancelled on SuperPDP"
        raise InvoicezError(msg)
    return remote


def paid(*documents: str, yes: bool = False) -> None:
    """Report invoices sent via SuperPDP as paid in full today (fr:212, Encaissée).

    The seller reports this status: buyers' platforms do not. Every invoice is
    checked before any is reported; those already paid are skipped.

    Args:
        documents: YAML files of the invoices, or their numbers (e.g. 2026-10-003).
        yes: Report without asking for confirmation.

    Raises:
        InvoicezError: If an invoice is a credit note, was not sent through
            SuperPDP, or is cancelled there.
    """
    if not documents:
        msg = "Give the invoices to report as paid"
        raise InvoicezError(msg)
    paths = Paths()
    console = Console()
    remote: dict[str, dict[str, Any]] = {}
    for invoice in list_sent_invoices():
        if (number := invoice_number(invoice)) is not None:
            # Ids increase: an invoice sent again replaces one found invalid.
            remote[number] = invoice

    # By number: a document can be given both by its file and by its number.
    to_report: dict[str, dict[str, Any]] = {}
    table = Table("Number", "Company", "Total incl. VAT", title="To report as paid")
    for document in documents:
        content = safe_load(paths.resolve_document(document).read_text("utf8"))
        number = str(content["document_number"])
        invoice = _check(number, content, remote.get(number))
        if number in to_report:
            continue
        if has_status(invoice, PAID):
            console.print(f"{number} is already paid, skipped")
            continue
        totals = (invoice.get("en_invoice") or {}).get("totals") or {}
        table.add_row(
            number,
            str(content.get("company", "")),
            str(totals.get("total_with_vat", "")),
        )
        to_report[number] = invoice

    if not to_report:
        return
    console.print(table)
    if not yes and not confirm(f"Report {len(to_report)} as paid today? [y]es, [n]o: "):
        return
    for number, invoice in to_report.items():
        event = mark_paid(invoice["id"])
        console.print(f"{number}: {describe(event)}", highlight=False)
