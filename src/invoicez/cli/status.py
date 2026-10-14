from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from cyclopts import Parameter
from rich.console import Console
from rich.table import Table

from invoicez.gcal.reconcile import iter_invoices
from invoicez.paths import Paths
from invoicez.superpdp.invoices import invoice_number, list_sent_invoices
from invoicez.superpdp.statuses import (
    CANCELLED,
    PAID,
    describe,
    has_status,
    is_problem,
    latest_event,
    undelivered_since,
)

# States shown by default, in this order.
_ACTION_STATES = ("problem", "not delivered?", "not sent", "awaiting payment")
_STATE_STYLES = {
    "problem": "bold red",
    "not delivered?": "bold yellow",
    "not sent": "yellow",
}
# The buyer's platform usually confirms receiving an invoice within minutes.
UNDELIVERED_AFTER = timedelta(days=2)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Row:
    state: str
    number: str
    date: str
    company: str
    status: str
    file: str


def _state(
    remote: dict[str, Any] | None,
    local: dict[str, Any] | None,
    cancelled_locally: bool,
) -> tuple[str, str]:
    if remote is None:
        return ("cancelled" if cancelled_locally else "not sent"), ""
    latest = latest_event(remote)
    status = describe(latest) if latest else "no status yet"
    if latest is not None and is_problem(latest) and not cancelled_locally:
        return "problem", status
    if has_status(remote, PAID):
        return "paid", status
    if cancelled_locally or has_status(remote, CANCELLED):
        return "cancelled", status
    emitted = undelivered_since(remote)
    if emitted is not None and _now() - emitted > UNDELIVERED_AFTER:
        return "not delivered?", f"{status} since {emitted:%Y-%m-%d}"
    if local is not None and "cancels" in local:
        return "sent", status
    return "awaiting payment", status


def status(
    *, show_all: Annotated[bool, Parameter(name="--all", negative="")] = False
) -> int:
    """Show the invoices to send, and the lifecycle of those sent via SuperPDP.

    By default, only invoices needing action are shown: those SuperPDP reports a
    problem for, those the buyer's platform has not confirmed receiving after two
    days, those not sent yet (dated on or after the first one SuperPDP has, as
    older ones were sent before it), and those awaiting payment.

    Args:
        show_all: Also show paid, cancelled, and older invoices, and sent credit
            notes.

    Returns:
        1 if SuperPDP reports a problem with an invoice, else 0.
    """
    paths = Paths()
    local = {
        str(content["document_number"]): (path, content)
        for path, content in iter_invoices(paths.git_dir)
    }
    cancelled = {
        str(content["cancels"]) for _, content in local.values() if "cancels" in content
    }
    remote: dict[str, dict[str, Any]] = {}
    for invoice in list_sent_invoices():
        if (number := invoice_number(invoice)) is not None:
            # Ids increase: an invoice sent again replaces one found invalid.
            remote[number] = invoice
    first_sent = min(
        (str(invoice["en_invoice"].get("issue_date")) for invoice in remote.values()),
        default=None,
    )

    rows: list[_Row] = []
    for number in sorted(local.keys() | remote.keys()):
        path, content = local.get(number, (None, None))
        invoice = remote.get(number)
        state, status_text = _state(invoice, content, number in cancelled)
        if content is not None:
            # Credit notes take their company from the invoice they cancel.
            _, cancelled_content = local.get(str(content.get("cancels")), (None, {}))
            company = content.get("company") or cancelled_content.get("company", "")
            date = str(content.get("date"))
        else:
            date, company = str(remote[number]["en_invoice"].get("issue_date")), ""
        if state not in _ACTION_STATES and not show_all:
            continue
        if (
            state == "not sent"
            and not show_all
            and first_sent is not None
            and date < first_sent
        ):
            continue
        rows.append(
            _Row(
                state=state,
                number=number,
                date=date,
                company=company,
                status=status_text,
                file=str(path.relative_to(paths.git_dir)) if path else "",
            )
        )

    order = {state: i for i, state in enumerate(_ACTION_STATES)}
    rows.sort(key=lambda r: (order.get(r.state, len(order)), r.number))
    table = Table(
        "State", "Number", "Date", "Company", "SuperPDP status", "File",
        title="Invoices" if show_all else "Invoices needing action",
    )  # fmt: skip
    for row in rows:
        style = _STATE_STYLES.get(row.state)
        table.add_row(
            f"[{style}]{row.state}[/]" if style else row.state,
            row.number,
            row.date,
            row.company,
            row.status,
            row.file,
        )
    console = Console()
    console.print(table)
    hidden = len(local.keys() | remote.keys()) - len(rows)
    if hidden:
        console.print(f"{hidden} other invoices (paid, cancelled, or older): --all")
    return 1 if any(row.state == "problem" for row in rows) else 0
