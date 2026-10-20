import csv as csv_module
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table
from yaml import safe_load

from invoicez.gcal.loading import analyse, load_events, load_gcal
from invoicez.gcal.reconcile import scan_documents
from invoicez.ledger.entries import Entry, load_entries
from invoicez.ledger.forecast import forecast_entries
from invoicez.ledger.report import (
    GROUPS,
    Group,
    Line,
    Summary,
    ledger_lines,
    summarize,
)
from invoicez.ledger.settings import LedgerSettings
from invoicez.paths import Paths
from invoicez.settings import Settings
from invoicez.superpdp.invoices import list_sent_invoices

_CSV_COLUMNS = (
    "number", "date", "company", "reference", "days", "trainees", "principal",
    "expenses", "amount", "vat", "contributions", "income_tax", "net", "sent",
    "paid", "source",
)  # fmt: skip
_LABELS: dict[Group, str] = {
    "paid": "Paid",
    "sent": "Sent",
    "to_bill": "To bill",
    "sure": "Sure",
    "tentative": "Tentative",
}


def _today() -> date:
    return date.today()


def _optional(value: object) -> str:
    return "" if value is None else str(value)


def _amount(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _write_csv(lines: list[Line], forecast: bool) -> None:
    writer = csv_module.writer(sys.stdout)
    writer.writerow(_CSV_COLUMNS + (("forecast",) if forecast else ()))
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
            ) + ((_optional(entry.forecast),) if forecast else ())
        )  # fmt: skip


def _invoice_table(title: str, lines: list[Line], paid: bool) -> Table:
    table = Table(
        "Invoice", "Date", "Company", "Reference", "Days", "Trainees", "Excl. VAT",
        "Expenses", "VAT", "Contrib.", "Income tax", "Net", "Sent",
        *(("Paid",) if paid else ()),
        title=title,
        caption=(
            "Dimmed: read from the spreadsheet."
            if any(line.entry.source == "spreadsheet" for line in lines)
            else None
        ),
    )  # fmt: skip
    for line in lines:
        entry = line.entry
        table.add_row(
            entry.number, str(entry.date), entry.company, entry.reference or "",
            f"{entry.days:g}", _optional(entry.trainees), _amount(entry.amount),
            _amount(entry.expenses), _amount(entry.vat), _optional(line.contributions),
            _optional(line.income_tax), _amount(line.net), _optional(entry.sent),
            *((_optional(entry.paid),) if paid else ()),
            style="dim" if entry.source == "spreadsheet" else None,
        )  # fmt: skip
    return table


def _forecast_table(title: str, lines: list[Line], caption: str) -> Table:
    table = Table(
        "Date", "Company", "Reference", "Days", "Trainees", "Excl. VAT", "VAT",
        "Contrib.", "Income tax", "Net",
        title=title,
        caption=caption,
    )  # fmt: skip
    for line in lines:
        entry = line.entry
        table.add_row(
            str(entry.date), entry.company, entry.reference or "",
            f"{entry.days:g}", _optional(entry.trainees), _amount(entry.amount),
            _amount(entry.vat), _optional(line.contributions),
            _optional(line.income_tax), _amount(line.net),
            style="dim" if entry.forecast == "tentative" else None,
        )  # fmt: skip
    return table


def _known_sum(values: Sequence[float | None]) -> float | None:
    # None if one is unknown.
    known = [value for value in values if value is not None]
    return sum(known) if len(known) == len(values) else None


def _totals_table(summary: Summary) -> Table:
    table = Table(
        "", "Days", "Trainees", "Excl. VAT", "Contrib.", "Income tax", "Net",
        title=f"{summary.year}: totals",
        show_footer=True,
    )  # fmt: skip
    tallies = summary.tallies
    for tally in tallies:
        table.add_row(
            _LABELS[tally.group], f"{tally.days:g}", str(tally.trainees),
            _amount(tally.amount), _optional(tally.contributions),
            _optional(tally.income_tax), _amount(tally.net),
            style="dim" if tally.group == "tentative" else None,
        )  # fmt: skip
    nets = _known_sum([tally.net for tally in tallies])
    footers = (
        "Total",
        f"{sum(tally.days for tally in tallies):g}",
        str(sum(tally.trainees for tally in tallies)),
        _amount(round(sum(tally.amount for tally in tallies), 2)),
        _optional(_known_sum([tally.contributions for tally in tallies])),
        _optional(_known_sum([tally.income_tax for tally in tallies])),
        _amount(None if nets is None else round(nets, 2)),
    )
    for column, footer in zip(table.columns, footers, strict=True):
        column.footer = footer
    return table


def _summary_text(summary: Summary) -> str:
    text = f"{summary.year}: {summary.invoices} invoices"
    if summary.unknown_trainees:
        text += f" ([yellow]{summary.unknown_trainees} without trainees[/])"
    return text + f"; collected during the year: {summary.collected:.2f} excl. VAT"


def _forecast(
    paths: Paths, settings: LedgerSettings, today: date, events: Path | None
) -> tuple[list[Entry], list[tuple[date, str]]]:
    context = load_gcal(paths.settings, paths.variables)
    analysis = analyse(
        load_events(context.gcal, events_file=events),
        context.gcal,
        scan_documents(paths.git_dir),
        today=today,
    )
    return forecast_entries(analysis, context, paths.git_dir, settings, today=today)


def ledger(
    *years: int,
    csv: bool = False,
    forecast: bool = False,
    events: Path | None = None,
) -> int:
    """Show the invoices of years with their contributions, income tax and net.

    Invoices dated before ledger.pivot_date in settings.yml are read from the
    spreadsheet kept by hand, later ones from the documents and SuperPDP.
    Contributions and income tax are estimates, from the rates and brackets of
    settings.yml.

    Args:
        years: Years to show. Defaults to the current year.
        csv: Write the invoices as CSV on the standard output instead.
        forecast: Also show the calendar's engagements not invoiced yet: over
            (to bill), confirmed or tentative, dated when they would be billed.
            Their contributions and income tax come after those of the
            invoices, from the most to the least certain.
        events: With forecast, read the events from this JSON file instead of
            Google Calendar.

    Returns:
        0.
    """
    paths = Paths()
    settings = Settings.from_path(paths.settings).ledger or LedgerSettings()
    global_mapping = safe_load(paths.variables.read_text(encoding="utf8"))
    entries, entry_warnings = load_entries(
        paths.git_dir, global_mapping, settings, list_sent_invoices
    )
    warnings = [(entry.date, warning) for entry, warning in entry_warnings]
    today = _today()
    shown = sorted(set(years)) or [today.year]
    # Forecasts are dated today or later: none in past years.
    if forecast and shown[-1] >= today.year:
        forecasts, forecast_warnings = _forecast(paths, settings, today, events)
        entries = sorted(
            [*entries, *forecasts], key=lambda entry: (entry.date, entry.number)
        )
        warnings.extend(forecast_warnings)
    lines = ledger_lines(entries, settings)
    if csv:
        _write_csv([line for line in lines if line.entry.date.year in shown], forecast)
        return 0
    console = Console()
    for year in shown:
        summary = summarize(lines, year)
        year_lines = [line for line in lines if line.entry.date.year == year]
        by_group = {
            group: [line for line in year_lines if line.group == group]
            for group in GROUPS
        }
        tables = [
            _invoice_table(f"{year}: paid", by_group["paid"], paid=True),
            _invoice_table(f"{year}: sent", by_group["sent"], paid=False),
            _forecast_table(
                f"{year}: to bill",
                by_group["to_bill"],
                "Over but not invoiced: invoicez gcal bill.",
            ),
            _forecast_table(
                f"{year}: forecast",
                [line for line in year_lines if line.group in ("sure", "tentative")],
                "Dimmed: tentative.",
            ),
        ]
        for table in [*(t for t in tables if t.row_count), _totals_table(summary)]:
            console.print()
            console.print(table)
        console.print(_summary_text(summary))
        for day, warning in warnings:
            if day.year == year:
                console.print(f"[yellow]{warning}[/]")
    return 0
