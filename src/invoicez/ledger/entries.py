"""Gather the invoices of the ledger, from the spreadsheet or from the documents.

Invoices dated before the pivot date are read from the spreadsheet kept by
hand; later ones from the documents, with their sending and payment dates from
SuperPDP. Credit notes count negatively.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from invoicez.cancellation import resolve_cancellation
from invoicez.exceptions import InvoicezError
from invoicez.gcal.reconcile import iter_invoices
from invoicez.ledger.settings import LedgerSettings
from invoicez.ledger.spreadsheet import Row, read_sheets
from invoicez.superpdp.invoices import invoice_number
from invoicez.superpdp.statuses import emitted_at, paid_at
from invoicez.variables import InvoiceLine, Variables

_CREDIT_NOTE = "381"
# Engagements of the calendar not invoiced yet: over (to bill), confirmed and to
# come (sure), or unconfirmed (tentative).
Forecast = Literal["to_bill", "sure", "tentative"]
# Two invoices differing by less are taken as equal.
_CENT = 0.005


class Entry(BaseModel, extra="forbid", frozen=True):
    number: str
    date: date
    company: str
    reference: str | None
    # Training days and people trained, for the training activity report.
    days: float
    trainees: int | None
    # Excluding VAT; expenses are those passed on to the client.
    principal: float
    expenses: float
    vat: float
    sent: date | None
    paid: date | None
    credit_note: bool
    # The number of the invoice a credit note cancels, if read from the documents.
    cancels: str | None = None
    source: Literal["spreadsheet", "documents", "calendar"]
    # Only for entries of the calendar, whose number is the engagement's id.
    forecast: Forecast | None = None

    @property
    def amount(self) -> float:
        return round(self.principal + self.expenses, 2)


def _number(value: Any) -> float:
    return value if isinstance(value, float) else 0.0


def _date(value: Any) -> date | None:
    return value if isinstance(value, date) else None


def _row_date(row: Row, number: str, year: int, dates: Mapping[str, date]) -> date:
    # The document is more reliable than the sending date typed by hand.
    if number in dates:
        return dates[number]
    if sent := _date(row.get("Envoyée")):
        return sent
    if match := re.match(r"(\d{4})-(\d{2})-", number):
        return date(int(match[1]), int(match[2]), 1)
    return date(year, 1, 1)


def _spreadsheet_entry(
    row: Row, number: str, year: int, dates: Mapping[str, date]
) -> Entry:
    if "Principal HT" in row:
        principal = _number(row.get("Principal HT"))
        expenses = _number(row.get("Frais HT"))
    else:
        principal, expenses = _number(row.get("HT")), 0.0
    trainees = row.get("Stagiaires")
    reference = row.get("Formation")
    # Credit notes are typed as negative invoices.
    return Entry(
        number=number,
        date=_row_date(row, number, year, dates),
        company=str(row.get("Entreprise") or ""),
        reference=str(reference) if reference is not None else None,
        days=_number(row.get("Jours")),
        trainees=round(trainees) if isinstance(trainees, float) else None,
        principal=principal,
        expenses=expenses,
        vat=_number(row.get("TVA")),
        sent=_date(row.get("Envoyée")),
        paid=_date(row.get("Payée")),
        credit_note=principal + expenses < 0,
        source="spreadsheet",
    )


def spreadsheet_entries(
    path: Path, dates: Mapping[str, date], before: date
) -> list[Entry]:
    """Read the invoices dated before a day from the spreadsheet.

    Each sheet named after a year lists invoices, one per row, under the columns
    Facture, Entreprise, Formation, Jours, Stagiaires, HT (or Principal HT and
    Frais HT), TVA, Envoyée and Payée. Rows without a number are left out.

    Args:
        path: The spreadsheet.
        dates: Dates of the documents, by number, to date the rows.
        before: Rows dated on or after this day are left out.

    Returns:
        The entries, in the order of the sheets.
    """
    entries = []
    for name, rows in read_sheets(path).items():
        if not name.isdigit():
            continue
        for row in rows:
            if not (number := row.get("Facture")):
                continue
            entry = _spreadsheet_entry(row, str(number), int(name), dates)
            if entry.date < before:
                entries.append(entry)
    return entries


def document_entry(
    content: Mapping[str, Any],
    global_mapping: Mapping[str, Any],
    git_dir: Path,
    settings: LedgerSettings,
    remote: dict[str, Any] | None,
    cancelled: Mapping[str, Any] | None,
) -> Entry:
    """Read an invoice or credit note document as an entry.

    Args:
        content: The document.
        global_mapping: The content of variables.yml.
        git_dir: The data repository, to resolve the invoice a credit note
            cancels.
        settings: The ledger settings.
        remote: The invoice as sent to SuperPDP, if it was.
        cancelled: The document of the invoice a credit note cancels.

    Returns:
        The entry.
    """
    variables = Variables.from_mappings(
        global_mapping, resolve_cancellation(dict(content), git_dir)
    )
    credit_note = variables.invoice_meta.type_code == _CREDIT_NOTE
    # A credit note's lines read "Avoir pour « ... »": the patterns are matched
    # against those of the invoice it cancels.
    lines = (
        [InvoiceLine.model_validate(line) for line in cancelled["products"]]
        if cancelled is not None
        else variables.products
    )

    def signed(value: float) -> float:
        # Adding 0.0 turns the -0.0 of empty credit notes into 0.0.
        return round(-value if credit_note else value, 2) + 0.0

    expense = re.compile(settings.expense_pattern, re.IGNORECASE)
    training_day = re.compile(settings.training_day_pattern)
    principal = expenses = days = 0.0
    for line in lines:
        if expense.search(line.description):
            expenses += line.pu * line.n
        else:
            principal += line.pu * line.n
        if line.unit == "DAY" and training_day.search(line.description):
            days += line.n
    # As on the invoice: the VAT of the total, to the cent.
    vat = round((principal + expenses) * variables.tva.rate, 2)
    sent = emitted_at(remote) if remote is not None else None
    paid = paid_at(remote) if remote is not None else None
    trainees = variables.trainees
    return Entry(
        number=variables.document_number,
        date=variables.date,
        company=variables.company.name,
        reference=variables.reference,
        days=signed(days),
        trainees=None if trainees is None else round(signed(trainees)),
        principal=signed(principal),
        expenses=signed(expenses),
        vat=signed(vat),
        sent=sent.date() if sent else None,
        paid=paid.date() if paid else None,
        credit_note=credit_note,
        cancels=str(content["cancels"]) if "cancels" in content else None,
        source="documents",
    )


def _check(
    entry: Entry, content: Mapping[str, Any], remote: dict[str, Any] | None
) -> list[str]:
    warnings = []
    if entry.trainees is None and not entry.credit_note:
        warnings.append(f"{entry.number}: no trainees in its document")
    if remote is not None:
        totals = (remote.get("en_invoice") or {}).get("totals") or {}
        sent_amount = totals.get("total_without_vat")
        if (
            sent_amount is not None
            and abs(abs(entry.amount) - float(sent_amount)) > _CENT
        ):
            warnings.append(
                f"{entry.number}: {abs(entry.amount):.2f} excluding VAT in its "
                f"document, but {float(sent_amount):.2f} sent to SuperPDP"
            )
    return warnings


def load_entries(
    git_dir: Path,
    global_mapping: Mapping[str, Any],
    settings: LedgerSettings,
    list_sent: Callable[[], Iterable[dict[str, Any]]],
) -> tuple[list[Entry], list[tuple[Entry, str]]]:
    """Gather every invoice and credit note of the ledger.

    Args:
        git_dir: The data repository.
        global_mapping: The content of variables.yml.
        settings: The ledger settings.
        list_sent: Returns the invoices sent through SuperPDP; only called if
            some invoices are read from the documents.

    Returns:
        The entries by date then number, and warnings about missing or
        inconsistent data, with the entry they are about.

    Raises:
        InvoicezError: If a document read is invalid.
    """
    documents = {
        str(content["document_number"]): (path, content)
        for path, content in iter_invoices(git_dir)
    }
    dates = {
        number: date.fromisoformat(str(content["date"]))
        for number, (_, content) in documents.items()
    }
    entries: list[Entry] = []
    if settings.pivot_date is not None and settings.spreadsheet is not None:
        entries = spreadsheet_entries(
            git_dir / settings.spreadsheet, dates, settings.pivot_date
        )
    recent = sorted(
        number
        for number, day in dates.items()
        if settings.pivot_date is None or day >= settings.pivot_date
    )
    remote: dict[str, dict[str, Any]] = {}
    if recent:
        for invoice in list_sent():
            if (number := invoice_number(invoice)) is not None:
                # Ids increase: an invoice sent again replaces one found invalid.
                remote[number] = invoice
    warnings: list[tuple[Entry, str]] = []
    for number in recent:
        path, content = documents[number]
        # Unknown cancelled invoices are reported by resolve_cancellation.
        _, cancelled = documents.get(str(content.get("cancels")), (None, None))
        try:
            entry = document_entry(
                content,
                global_mapping,
                git_dir,
                settings,
                remote.get(number),
                cancelled,
            )
        except ValidationError as e:
            msg = f"{path}: invalid document\n{e}"
            raise InvoicezError(msg) from e
        entries.append(entry)
        warnings.extend(
            (entry, warning) for warning in _check(entry, content, remote.get(number))
        )
    entries.sort(key=lambda entry: (entry.date, entry.number))
    return entries, warnings
