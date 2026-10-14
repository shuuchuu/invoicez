"""Estimate what each invoice leaves once contributions and income tax are paid."""

from collections.abc import Iterable, Sequence
from itertools import groupby

from pydantic import BaseModel

from invoicez.ledger.entries import Entry
from invoicez.ledger.settings import LedgerSettings
from invoicez.ledger.taxes import contribution_rate, income_tax, to_euros


class Line(BaseModel, extra="forbid", frozen=True):
    entry: Entry
    # In euros; None without a rate or brackets for the invoice's date.
    contributions: int | None
    income_tax: int | None

    @property
    def net(self) -> float | None:
        if self.contributions is None or self.income_tax is None:
            return None
        return round(self.entry.amount - self.contributions - self.income_tax, 2)


class Summary(BaseModel, extra="forbid", frozen=True):
    year: int
    invoices: int
    days: float
    trainees: int
    # Invoices, credit notes aside, without a number of trainees.
    unknown_trainees: int
    amount: float
    expenses: float
    vat: float
    contributions: int | None
    income_tax: int | None
    # Amount of the invoices paid during the year, whatever their date: the
    # turnover to declare, micro-entrepreneurs being taxed on what they collect.
    collected: float


def _year_lines(entries: Sequence[Entry], settings: LedgerSettings) -> list[Line]:
    # The income tax of an invoice is what it adds to the tax of the year so far.
    tax = settings.income_tax
    brackets = tax.brackets.get(entries[0].date.year) if tax is not None else None
    lines = []
    taxable = 0.0
    for entry in entries:
        rate = contribution_rate(settings.contribution_rates, entry.date)
        contributions = to_euros(entry.amount * rate) if rate is not None else None
        added: int | None = None
        if tax is not None and brackets is not None:
            before = income_tax(taxable, brackets, tax.shares)
            taxable += entry.amount * (1 - tax.allowance)
            added = to_euros(income_tax(taxable, brackets, tax.shares) - before)
        lines.append(Line(entry=entry, contributions=contributions, income_tax=added))
    return lines


def ledger_lines(entries: Iterable[Entry], settings: LedgerSettings) -> list[Line]:
    """Estimate the contributions and income tax of each invoice.

    Contributions take the rate in force on the invoice's date. The income tax of
    a year is spread over its invoices, in order, each bearing the tax its amount
    adds at the top of the progressive scale.

    Args:
        entries: The entries, by date.
        settings: The ledger settings.

    Returns:
        The lines, in the same order.
    """
    lines: list[Line] = []
    for _, year_entries in groupby(entries, key=lambda entry: entry.date.year):
        lines.extend(_year_lines(list(year_entries), settings))
    return lines


def _total(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if len(known) == len(values) else None


def summarize(lines: Sequence[Line], year: int) -> Summary:
    """Total the lines of a year.

    Args:
        lines: Every line of the ledger, for the amounts collected during the
            year from invoices of the year before.
        year: The year to total.

    Returns:
        The totals.
    """
    of_year = [line for line in lines if line.entry.date.year == year]
    entries = [line.entry for line in of_year]
    return Summary(
        year=year,
        invoices=len(entries),
        days=sum(entry.days for entry in entries),
        trainees=sum(entry.trainees or 0 for entry in entries),
        unknown_trainees=sum(
            entry.trainees is None and not entry.credit_note for entry in entries
        ),
        amount=round(sum(entry.amount for entry in entries), 2),
        expenses=round(sum(entry.expenses for entry in entries), 2),
        vat=round(sum(entry.vat for entry in entries), 2),
        contributions=_total([line.contributions for line in of_year]),
        income_tax=_total([line.income_tax for line in of_year]),
        collected=round(
            sum(
                line.entry.amount
                for line in lines
                if line.entry.paid is not None and line.entry.paid.year == year
            ),
            2,
        ),
    )
