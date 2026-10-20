"""Estimate what each invoice leaves once contributions and income tax are paid."""

from collections.abc import Iterable, Sequence, Set
from itertools import groupby
from typing import Literal

from pydantic import BaseModel

from invoicez.ledger.entries import Entry, Forecast
from invoicez.ledger.settings import LedgerSettings
from invoicez.ledger.taxes import contribution_rate, income_tax, to_euros

# Invoices paid or not, then forecasts from the most to the least certain.
Group = Literal["paid", "sent", "to_bill", "sure", "tentative"]
GROUPS: tuple[Group, ...] = ("paid", "sent", "to_bill", "sure", "tentative")
# Invoices first, then forecasts from the most to the least certain.
_CERTAINTY: tuple[Forecast | None, ...] = (None, "to_bill", "sure", "tentative")


class Line(BaseModel, extra="forbid", frozen=True):
    entry: Entry
    group: Group
    # In euros; None without a rate or brackets for the invoice's date.
    contributions: int | None
    income_tax: int | None

    @property
    def net(self) -> float | None:
        if self.contributions is None or self.income_tax is None:
            return None
        return round(self.entry.amount - self.contributions - self.income_tax, 2)


class Tally(BaseModel, extra="forbid", frozen=True):
    """Totals of the lines of a year in the same group."""

    group: Group
    days: float
    trainees: int
    amount: float
    contributions: int | None
    income_tax: int | None
    net: float | None


class Summary(BaseModel, extra="forbid", frozen=True):
    year: int
    invoices: int
    # Invoices, credit notes aside, without a number of trainees.
    unknown_trainees: int
    # Amount of the invoices paid during the year, whatever their date: the
    # turnover to declare, micro-entrepreneurs being taxed on what they collect.
    collected: float
    # Paid and sent always, forecasts only when there are some.
    tallies: tuple[Tally, ...]


def _group(entry: Entry, paid: Set[str]) -> Group:
    if entry.forecast is not None:
        return entry.forecast
    # A credit note goes with the invoice it cancels.
    if entry.paid is not None or entry.cancels in paid:
        return "paid"
    return "sent"


def _year_lines(
    entries: Sequence[Entry], settings: LedgerSettings, paid: Set[str]
) -> list[Line]:
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
        lines.append(
            Line(
                entry=entry,
                group=_group(entry, paid),
                contributions=contributions,
                income_tax=added,
            )
        )
    return lines


def ledger_lines(entries: Iterable[Entry], settings: LedgerSettings) -> list[Line]:
    """Estimate the contributions and income tax of each invoice.

    Contributions take the rate in force on the invoice's date. The income tax of
    a year is spread over its invoices, in order, each bearing the tax its amount
    adds at the top of the progressive scale. Forecasts come after the invoices,
    from the most to the least certain: they never raise the tax of what is
    more certain.

    Args:
        entries: The entries, by date.
        settings: The ledger settings.

    Returns:
        The lines, in the same order.
    """
    entries = list(entries)
    paid = {entry.number for entry in entries if entry.paid is not None}
    # Stable: by date within each certainty.
    order = sorted(
        range(len(entries)),
        key=lambda i: (entries[i].date.year, _CERTAINTY.index(entries[i].forecast)),
    )
    lines: dict[int, Line] = {}
    for _, year_order in groupby(order, key=lambda i: entries[i].date.year):
        indices = list(year_order)
        year_lines = _year_lines([entries[i] for i in indices], settings, paid)
        lines.update(zip(indices, year_lines, strict=True))
    return [lines[i] for i in range(len(entries))]


def _total(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if len(known) == len(values) else None


def _net(lines: Sequence[Line]) -> float | None:
    nets = [line.net for line in lines]
    known = [net for net in nets if net is not None]
    return round(sum(known), 2) if len(known) == len(nets) else None


def _tally(group: Group, lines: Sequence[Line]) -> Tally:
    return Tally(
        group=group,
        days=sum(line.entry.days for line in lines),
        trainees=sum(line.entry.trainees or 0 for line in lines),
        amount=round(sum(line.entry.amount for line in lines), 2),
        contributions=_total([line.contributions for line in lines]),
        income_tax=_total([line.income_tax for line in lines]),
        net=_net(lines),
    )


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
    entries = [line.entry for line in of_year if line.entry.forecast is None]
    by_group = {
        group: [line for line in of_year if line.group == group] for group in GROUPS
    }
    return Summary(
        year=year,
        invoices=len(entries),
        unknown_trainees=sum(
            entry.trainees is None and not entry.credit_note for entry in entries
        ),
        collected=round(
            sum(
                line.entry.amount
                for line in lines
                if line.entry.paid is not None and line.entry.paid.year == year
            ),
            2,
        ),
        tallies=tuple(
            _tally(group, group_lines)
            for group, group_lines in by_group.items()
            if group_lines or group in ("paid", "sent")
        ),
    )
