import csv as csv_module
import sys
from datetime import date

from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.ledger.entries import load_entries
from invoicez.ledger.report import Line, Summary, ledger_lines, summarize
from invoicez.ledger.settings import LedgerSettings
from invoicez.paths import Paths
from invoicez.settings import Settings
from invoicez.superpdp.invoices import list_sent_invoices

_CSV_COLUMNS = (
    "number", "date", "company", "reference", "days", "trainees", "principal",
    "expenses", "amount", "vat", "contributions", "income_tax", "net", "sent",
    "paid", "source",
)  # fmt: skip


def _today() -> date:
    return date.today()


def _optional(value: object) -> str:
    return "" if value is None else str(value)


def _amount(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _write_csv(lines: list[Line]) -> None:
    writer = csv_module.writer(sys.stdout)
    writer.writerow(_CSV_COLUMNS)
    for line in lines:
        entry = line.entry
        writer.writerow(
            (
                entry.number, entry.date, entry.company, _optional(entry.reference),
                f"{entry.days:g}", _optional(entry.trainees), _amount(entry.principal),
                _amount(entry.expenses), _amount(entry.amount), _amount(entry.vat),
                _optional(line.contributions), _optional(line.income_tax),
                _amount(line.net), _optional(entry.sent), _optional(entry.paid),
                entry.source,
            )
        )  # fmt: skip


def _table(year: int, lines: list[Line], summary: Summary) -> Table:
    table = Table(
        "Invoice", "Date", "Company", "Reference", "Days", "Trainees", "Excl. VAT",
        "Expenses", "VAT", "Contrib.", "Income tax", "Net", "Sent", "Paid",
        title=str(year),
        caption="Dimmed: read from the spreadsheet.",
        show_footer=True,
    )  # fmt: skip
    for line in lines:
        entry = line.entry
        table.add_row(
            entry.number, str(entry.date), entry.company, entry.reference or "",
            f"{entry.days:g}", _optional(entry.trainees), _amount(entry.amount),
            _amount(entry.expenses), _amount(entry.vat), _optional(line.contributions),
            _optional(line.income_tax), _amount(line.net), _optional(entry.sent),
            _optional(entry.paid),
            style="dim" if entry.source == "spreadsheet" else None,
        )  # fmt: skip
    footers = {
        "Invoice": "Total",
        "Days": f"{summary.days:g}",
        "Trainees": str(summary.trainees),
        "Excl. VAT": _amount(summary.amount),
        "Expenses": _amount(summary.expenses),
        "VAT": _amount(summary.vat),
        "Contrib.": _optional(summary.contributions),
        "Income tax": _optional(summary.income_tax),
    }
    for column in table.columns:
        column.footer = footers.get(str(column.header), "")
    return table


def _summary_text(summary: Summary) -> str:
    text = (
        f"{summary.year}: {summary.invoices} invoices, {summary.days:g} training "
        f"days, {summary.trainees} trainees"
    )
    if summary.unknown_trainees:
        text += f" ([yellow]{summary.unknown_trainees} invoices without trainees[/])"
    text += f"; collected during the year: {summary.collected:.2f} excl. VAT"
    if summary.contributions is not None and summary.income_tax is not None:
        net = summary.amount - summary.contributions - summary.income_tax
        text += f"; net after contributions and income tax: {net:.2f}"
    return text


def ledger(*years: int, csv: bool = False) -> int:
    """Show the invoices of years with their contributions, income tax and net.

    Invoices dated before ledger.pivot_date in settings.yml are read from the
    spreadsheet kept by hand, later ones from the documents and SuperPDP.
    Contributions and income tax are estimates, from the rates and brackets of
    settings.yml.

    Args:
        years: Years to show. Defaults to the current year.
        csv: Write the invoices as CSV on the standard output instead.

    Returns:
        0.
    """
    paths = Paths()
    settings = Settings.from_path(paths.settings).ledger or LedgerSettings()
    global_mapping = safe_load(paths.variables.read_text(encoding="utf8"))
    entries, warnings = load_entries(
        paths.git_dir, global_mapping, settings, list_sent_invoices
    )
    lines = ledger_lines(entries, settings)
    shown = sorted(set(years)) or [_today().year]
    if csv:
        _write_csv([line for line in lines if line.entry.date.year in shown])
        return 0
    console = Console()
    for year in shown:
        summary = summarize(lines, year)
        year_lines = [line for line in lines if line.entry.date.year == year]
        console.print(_table(year, year_lines, summary))
        console.print(_summary_text(summary))
        for entry, warning in warnings:
            if entry.date.year == year:
                console.print(f"[yellow]{warning}[/]")
    return 0
