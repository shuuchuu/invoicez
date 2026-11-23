import time
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from facturx import get_xml_from_pdf
from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.building.xml.variables_to_bt_converter import convert
from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.runner import load_document
from invoicez.runner import run as runner_run
from invoicez.superpdp.invoices import (
    OutcomeUnknownError,
    get_invoice,
    get_original_invoice,
    invoice_number,
    list_sent_invoices,
    send_invoice,
    validate_invoice,
)
from invoicez.superpdp.recovery import look_for
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


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().casefold().startswith("y")
    except EOFError:
        return False


def _is_invoice(invoice: dict, number: str) -> bool:
    return number in {invoice_number(invoice), invoice.get("external_id")}


def _status(invoice: dict) -> str:
    latest = latest_event(invoice)
    return describe(latest) if latest else "no status yet"


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
    buyer = bt["BG-7"]
    address = buyer.get("einvoicing_addr")
    if address is not None and buyer.get("einvoicing_addr_schemeid"):
        address = f"{buyer['einvoicing_addr_schemeid']}:{address}"
    grid = Table.grid(padding=(0, 2))
    grid.add_row(kind, f"{bt['BT-1']} of {bt['BT-2']}")
    grid.add_row("Buyer", buyer["name"])
    grid.add_row("Sent to", address or "[bold red]no electronic address[/]")
    grid.add_row("Total", f"{bt['BT-112']} {bt['BT-5']} incl. VAT")
    grid.add_row("PDF", str(pdf_path))
    console.print(grid)


def _send(console: Console, pdf_bytes: bytes, number: str, known_ids: set[int]) -> dict:
    """Send an invoice, finding it at SuperPDP if it does not confirm receiving it.

    Args:
        console: Where to print.
        pdf_bytes: PDF of the invoice.
        number: Number of the invoice.
        known_ids: SuperPDP ids of the invoices sent before, not to be mistaken for
            this one: those found invalid share their number with the next attempt.

    Returns:
        The invoice, with at least its SuperPDP id.

    Raises:
        InvoicezError: If the invoice was not sent, or may not have been.
    """
    try:
        return send_invoice(pdf_bytes, external_id=number)
    except OutcomeUnknownError as error:
        unknown = error
    console.print(
        f"{unknown}\nThe invoice may have been sent all the same, looking for it...",
        style="yellow",
        markup=False,
        highlight=False,
    )

    def find() -> dict | None:
        for invoice in list_sent_invoices():
            if invoice["id"] not in known_ids and _is_invoice(invoice, number):
                return invoice
        return None

    if (found := look_for(find)) is not None:
        return found
    msg = (
        f"{unknown}. Invoice {number} may have been sent all the same: wait a few "
        "minutes and check that SuperPDP does not have it with invoicez status "
        "before sending it again"
    )
    raise InvoicezError(msg) from unknown


def _wait_for_status(console: Console, invoice: dict) -> dict:
    deadline = time.monotonic() + STATUS_WAIT_SECONDS
    while True:
        failure = None
        try:
            invoice = get_invoice(invoice["id"])
        except InvoicezError as error:
            failure = error
        else:
            latest = latest_event(invoice)
            if latest is not None and latest["status_code"] != "api:uploaded":
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(STATUS_POLL_SECONDS)
    if failure is not None:
        console.print(
            f"The invoice was sent but its status could not be read, see invoicez "
            f"status later: {failure}",
            style="yellow",
            markup=False,
            highlight=False,
        )
    return invoice


def _archive_path(paths: Paths, pdf_path: Path, data: dict[str, Any]) -> Path:
    year = date.fromisoformat(str(data["date"])).year
    return paths.archive_dir / str(year) / pdf_path.name


def _archive(
    console: Console, paths: Paths, pdf_path: Path, archive_path: Path
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.rename(archive_path)
    console.print(
        f"Archived to {archive_path.relative_to(paths.git_dir)}", highlight=False
    )


def _is_as_sent(pdf_path: Path, invoice_id: int) -> bool:
    """Tell whether the XML in a PDF is the one SuperPDP was sent.

    Args:
        pdf_path: PDF of the invoice.
        invoice_id: SuperPDP id of the invoice.

    Returns:
        Whether they are the same, but for how the XML is serialized.
    """
    try:
        _, xml_bytes = get_xml_from_pdf(pdf_path.read_bytes())
    except Exception:
        # Damaged PDFs, such as truncated ones, make pypdf raise all sorts of errors.
        return False
    if not xml_bytes:
        return False
    original = get_original_invoice(invoice_id)
    try:
        return ElementTree.canonicalize(
            from_file=BytesIO(xml_bytes)
        ) == ElementTree.canonicalize(from_file=BytesIO(original))
    except ElementTree.ParseError:
        return False


def _archive_already_sent(
    console: Console, paths: Paths, path: Path, data: dict[str, Any], invoice: dict
) -> None:
    """Archive the PDF an interrupted sending left in sendbox/, if it is what was sent.

    Args:
        console: Where to print.
        paths: Paths of the repository.
        path: YAML file of the invoice.
        data: Content of the invoice.
        invoice: The invoice SuperPDP has.

    Raises:
        InvoicezError: If there is nothing to archive, or it is not what was sent.
    """
    msg = (
        f"Invoice {data['document_number']} was already sent "
        f"(SuperPDP id {invoice['id']}, {_status(invoice)})"
    )
    pdf_path = paths.sendbox_dir / f"{path.with_suffix('').name}.pdf"
    archive_path = _archive_path(paths, pdf_path, data)
    if not pdf_path.is_file() or archive_path.exists():
        raise InvoicezError(msg)
    relative_pdf_path = pdf_path.relative_to(paths.git_dir)
    try:
        as_sent = _is_as_sent(pdf_path, invoice["id"])
    except InvoicezError as error:
        msg += f", left {relative_pdf_path} there: {error}"
        raise InvoicezError(msg) from error
    if not as_sent:
        msg += f", left {relative_pdf_path} there as it is not what was sent"
        raise InvoicezError(msg)
    console.print(msg, markup=False, highlight=False)
    _archive(console, paths, pdf_path, archive_path)


def _raise_problems(invoice: dict) -> None:
    if issues := problems(invoice):
        msg = "\n".join(describe(event) for event in issues)
        raise InvoicezError(msg)


def send_document(
    console: Console,
    paths: Paths,
    path: Path,
    *,
    template: str = "main",
    validate: bool = True,
    yes: bool = False,
) -> Literal["sent", "skipped", "archived"]:
    """Build an invoice, send it via SuperPDP once confirmed, then archive its PDF.

    The PDF is built in sendbox/. Once sent, and unless SuperPDP finds it invalid,
    it moves to archive/<year>/. If SuperPDP already has the invoice, only its PDF
    left in sendbox/ by an interrupted sending is archived.

    Args:
        console: Where to print.
        paths: Paths of the repository.
        path: YAML file of the invoice.
        template: Template to use for the compilation.
        validate: Validate the invoice against SuperPDP before sending.
        yes: Send without asking for confirmation.

    Returns:
        "sent", "skipped" if sending was not confirmed, or "archived" if it was
        already sent and only its PDF had to be archived.

    Raises:
        InvoicezError: If the document is a quote, was already sent and archived, is
            invalid, SuperPDP rejects it, or may not have received it.
    """
    data = load_document(path, paths.git_dir)
    if data.get("quote"):
        msg = f"{path.name} is a quote, only invoices are sent"
        raise InvoicezError(msg)
    number = str(data["document_number"])
    sent_before = list_sent_invoices()
    for invoice in sent_before:
        if _is_invoice(invoice, number) and was_transmitted(invoice):
            _archive_already_sent(console, paths, path, data, invoice)
            _raise_problems(invoice)
            return "archived"

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
    if not yes and not confirm("Send it? [y]es, [n]o: "):
        console.print(f"Not sent, left in {relative_pdf_path}", highlight=False)
        return "skipped"

    known_ids = {invoice["id"] for invoice in sent_before}
    invoice = _wait_for_status(console, _send(console, pdf_bytes, number, known_ids))
    console.print(
        f"Invoice {number} sent with SuperPDP id {invoice['id']}: {_status(invoice)}",
        highlight=False,
    )
    latest = latest_event(invoice)
    if latest is not None and latest["status_code"] == INVALID:
        msg = f"SuperPDP found invoice {number} invalid, left in {relative_pdf_path}"
        raise InvoicezError(msg)

    archive_path = _archive_path(paths, pdf_path, data)
    if archive_path.exists():
        msg = (
            f"{archive_path.relative_to(paths.git_dir)} already exists, "
            f"left the sent invoice in {relative_pdf_path}"
        )
        raise InvoicezError(msg)
    _archive(console, paths, pdf_path, archive_path)
    _raise_problems(invoice)
    return "sent"
