import time
from datetime import date
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.building.xml.variables_to_bt_converter import convert
from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.runner import load_document
from invoicez.runner import run as runner_run
from invoicez.superpdp.invoices import (
    get_invoice,
    invoice_number,
    list_sent_invoices,
    send_invoice,
    validate_invoice,
)
from invoicez.superpdp.statuses import (
    INVALID,
    describe,
    latest_event,
    problems,
    was_transmitted,
)
from invoicez.variables import Variables

# How long to wait for SuperPDP to process a sent invoice before reporting.
STATUS_WAIT_SECONDS = 10.0
STATUS_POLL_SECONDS = 1.0


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().casefold().startswith("y")
    except EOFError:
        return False


def _check_not_sent(number: str) -> None:
    for invoice in list_sent_invoices():
        if invoice_number(invoice) == number and was_transmitted(invoice):
            latest = latest_event(invoice)
            status = describe(latest) if latest else "no status yet"
            msg = (
                f"Invoice {number} was already sent (SuperPDP id {invoice['id']}, "
                f"{status})"
            )
            raise InvoicezError(msg)


def _validate(console: Console, pdf_bytes: bytes) -> None:
    report = validate_invoice(pdf_bytes)["data"][0]
    if report["is_valid"]:
        return
    if report.get("error"):
        console.print(report["error"], highlight=False)
    for subreport in report.get("subreports") or []:
        console.print(subreport)
    msg = "Invoice validation failed, invoice not sent"
    raise InvoicezError(msg)


def _preview(console: Console, variables: Variables, pdf_path: Path) -> None:
    bt = convert(variables)
    kind = "Credit note" if bt["BT-3"] == "381" else "Invoice"
    address = bt.get("BT-49")
    if address is not None and bt.get("BT-49-1"):
        address = f"{bt['BT-49-1']}:{address}"
    grid = Table.grid(padding=(0, 2))
    grid.add_row(kind, f"{bt['BT-1']} of {bt['BT-2']}")
    grid.add_row("Buyer", bt["BT-44"])
    grid.add_row("Sent to", address or "[bold red]no electronic address[/]")
    grid.add_row("Total", f"{bt['BT-112']} {bt['BT-5']} incl. VAT")
    grid.add_row("PDF", str(pdf_path))
    console.print(grid)


def _wait_for_status(invoice_id: int) -> dict:
    deadline = time.monotonic() + STATUS_WAIT_SECONDS
    invoice = get_invoice(invoice_id)
    while time.monotonic() < deadline:
        latest = latest_event(invoice)
        if latest is not None and latest["status_code"] != "api:uploaded":
            break
        time.sleep(STATUS_POLL_SECONDS)
        invoice = get_invoice(invoice_id)
    return invoice


def send_document(
    console: Console,
    paths: Paths,
    path: Path,
    *,
    template: str = "main",
    validate: bool = True,
    yes: bool = False,
) -> Literal["sent", "skipped"]:
    """Build an invoice, send it via SuperPDP once confirmed, then archive its PDF.

    The PDF is built in sendbox/. Once sent, and unless SuperPDP finds it invalid,
    it moves to archive/<company>/<year>/.

    Args:
        console: Where to print.
        paths: Paths of the repository.
        path: YAML file of the invoice.
        template: Template to use for the compilation.
        validate: Validate the invoice against SuperPDP before sending.
        yes: Send without asking for confirmation.

    Returns:
        "sent", or "skipped" if sending was not confirmed.

    Raises:
        InvoicezError: If the document is a quote, was already sent, is invalid, or
            SuperPDP rejects it.
    """
    data = load_document(path, paths.git_dir)
    if data.get("quote"):
        msg = f"{path.name} is a quote, only invoices are sent"
        raise InvoicezError(msg)
    number = str(data["document_number"])
    _check_not_sent(number)

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
        _validate(console, pdf_bytes)

    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    relative_pdf_path = pdf_path.relative_to(paths.git_dir)
    _preview(console, Variables.from_mappings(global_data, data), relative_pdf_path)
    if not yes and not _confirm("Send it? [y]es, [n]o: "):
        console.print(f"Not sent, left in {relative_pdf_path}", highlight=False)
        return "skipped"

    invoice = _wait_for_status(send_invoice(pdf_bytes, external_id=number)["id"])
    latest = latest_event(invoice)
    status = describe(latest) if latest else "no status yet"
    console.print(
        f"Invoice {number} sent with SuperPDP id {invoice['id']}: {status}",
        highlight=False,
    )
    if latest is not None and latest["status_code"] == INVALID:
        msg = f"SuperPDP found invoice {number} invalid, left in {relative_pdf_path}"
        raise InvoicezError(msg)

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
    console.print(
        f"Archived to {archive_path.relative_to(paths.git_dir)}", highlight=False
    )

    if issues := problems(invoice):
        msg = "\n".join(describe(event) for event in issues)
        raise InvoicezError(msg)
    return "sent"
