from typing import Any

from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.sending import confirm
from invoicez.superpdp.invoices import (
    OutcomeUnknownError,
    by_number,
    get_invoice,
    list_sent_invoices,
    mark_paid,
)
from invoicez.superpdp.recovery import look_for
from invoicez.superpdp.statuses import (
    CANCELLED,
    PAID,
    describe,
    has_status,
    was_rejected,
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
    if was_rejected(remote):
        msg = f"{number} was rejected, see invoicez status"
        raise InvoicezError(msg)
    if has_status(remote, CANCELLED):
        msg = f"{number} is cancelled on SuperPDP"
        raise InvoicezError(msg)
    return remote


def _report(console: Console, number: str, invoice_id: int) -> dict:
    """Report an invoice as paid, checking it was if SuperPDP does not confirm it.

    Args:
        console: Where to print.
        number: Number of the invoice.
        invoice_id: SuperPDP id of the invoice, not paid yet.

    Returns:
        The event created.

    Raises:
        InvoicezError: If the invoice was not reported as paid, or may not have been.
    """
    try:
        return mark_paid(invoice_id)
    except OutcomeUnknownError as error:
        unknown = error
    console.print(
        f"{unknown}\n{number} may be reported as paid all the same, checking...",
        style="yellow",
        markup=False,
        highlight=False,
    )

    def find() -> dict | None:
        for event in get_invoice(invoice_id).get("events") or []:
            if event["status_code"] == PAID:
                return event
        return None

    if (found := look_for(find)) is not None:
        return found
    msg = (
        f"{unknown}. {number} may be reported as paid all the same: wait a few minutes "
        "before running invoicez paid again, which skips the invoices already paid. "
        "invoicez log shows the request, to tell SuperPDP"
    )
    raise InvoicezError(msg) from unknown


def paid(*documents: str, yes: bool = False) -> None:
    """Report invoices sent via SuperPDP as paid in full today (fr:212, Encaissée).

    The seller reports this status: buyers' platforms do not. Every invoice is
    checked before any is reported; those already paid are skipped.

    Args:
        documents: YAML files of the invoices, or their numbers (e.g. 2026-10-003).
        yes: Report without asking for confirmation.

    Raises:
        InvoicezError: If an invoice is a credit note, was not sent through
            SuperPDP, or is rejected or cancelled there, or if reporting one fails or
            may have: those after it are not reported.
    """
    if not documents:
        msg = "Give the invoices to report as paid"
        raise InvoicezError(msg)
    paths = Paths()
    console = Console()
    remote = by_number(list_sent_invoices())

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
        event = _report(console, number, invoice["id"])
        console.print(f"{number}: {describe(event)}", highlight=False)
