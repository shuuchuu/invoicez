from datetime import date

from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.exceptions import InvoicezError
from invoicez.ledger.declarations import Due, dues
from invoicez.ledger.entries import load_entries
from invoicez.ledger.settings import LedgerSettings
from invoicez.paths import Paths
from invoicez.settings import Settings
from invoicez.superpdp.invoices import list_sent_invoices

_TITLES = {"urssaf": "Social contributions (URSSAF)", "vat": "VAT"}


def _today() -> date:
    return date.today()


def _label(due: Due) -> str:
    if due.start.month == due.end.month:
        return f"{due.start:%Y-%m}"
    return f"{due.start.year}-Q{(due.start.month - 1) // 3 + 1}"


def _table(tax: str, rows: list[Due]) -> Table:
    table = Table(
        "Period", "Due by", "Invoices paid", "Collected excl. VAT", "Owed",
        title=_TITLES[tax],
        caption="Dimmed: period in progress.",
        show_footer=True,
    )  # fmt: skip
    for due in rows:
        table.add_row(
            _label(due), str(due.deadline), "\n".join(due.invoices),
            f"{due.collected:.2f}", "" if due.owed is None else str(due.owed),
            style="dim" if due.in_progress else None,
        )  # fmt: skip
    owed = [due.owed for due in rows]
    table.columns[0].footer = "Total"
    table.columns[3].footer = f"{sum(due.collected for due in rows):.2f}"
    table.columns[4].footer = (
        "" if None in owed else str(sum(o for o in owed if o is not None))
    )
    return table


def due() -> int:
    """Show what the payments received but not declared yet owe, by declaration.

    Social contributions and VAT are owed on the payments received in each
    period after ledger.declarations.<tax>.declared_through in settings.yml, up to
    the period in progress. Move declared_through on once a period is declared.
    VAT is the VAT collected: purchases are not deducted.

    Returns:
        0.

    Raises:
        InvoicezError: If no declaration is configured.
    """
    paths = Paths()
    settings = Settings.from_path(paths.settings).ledger or LedgerSettings()
    declarations = settings.declarations
    if declarations.urssaf is None and declarations.vat is None:
        msg = "Configure ledger.declarations in settings.yml to see what is owed"
        raise InvoicezError(msg)
    global_mapping = safe_load(paths.variables.read_text(encoding="utf8"))
    entries, _ = load_entries(
        paths.git_dir, global_mapping, settings, list_sent_invoices
    )
    rows, warnings = dues(entries, declarations, settings.contribution_rates, _today())
    console = Console()
    for tax in ("urssaf", "vat"):
        if tax_rows := [row for row in rows if row.tax == tax]:
            console.print()
            console.print(_table(tax, tax_rows))
    for warning in warnings:
        console.print(f"[yellow]{warning}[/]")
    return 0
