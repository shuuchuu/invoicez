"""Work out what is owed on the payments received but not declared yet.

Social contributions and VAT are both declared on what was received during each
period (VAT on services is due once paid), from the payment dates of the ledger.
"""

from calendar import monthrange
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from invoicez.ledger.entries import Entry
from invoicez.ledger.settings import Declaration, Declarations, next_period
from invoicez.ledger.taxes import contribution_rate, to_euros

Tax = Literal["urssaf", "vat"]


class Due(BaseModel, extra="forbid", frozen=True):
    """What a declaration not made yet owes."""

    tax: Tax
    start: date
    end: date
    deadline: date
    # The period is not over: more payments may come.
    in_progress: bool
    # The invoices paid during the period.
    invoices: tuple[str, ...]
    # Received during the period, excluding VAT.
    collected: float
    # In euros; None without a contribution rate for a payment.
    owed: int | None


def _deadline(end: date, declaration: Declaration) -> date:
    after = end + timedelta(days=1)
    day = declaration.due_day or monthrange(after.year, after.month)[1]
    return after.replace(day=day)


def _owed(tax: Tax, paid: list[Entry], rates: Mapping[date, float]) -> int | None:
    if tax == "vat":
        return to_euros(sum(entry.vat for entry in paid))
    owed = 0.0
    for entry in paid:
        assert entry.paid is not None
        rate = contribution_rate(rates, entry.paid)
        if rate is None:
            return None
        owed += entry.amount * rate
    return to_euros(owed)


def _periods(declaration: Declaration, today: date) -> Iterable[tuple[date, date]]:
    # The periods after the last declared, up to the one of today.
    start = declaration.declared_through + timedelta(days=1)
    while start <= today:
        following = next_period(start, declaration.period)
        yield start, following - timedelta(days=1)
        start = following


def dues(
    entries: Iterable[Entry],
    declarations: Declarations,
    rates: Mapping[date, float],
    today: date,
) -> tuple[list[Due], list[str]]:
    """List the declarations not made yet, with what each owes.

    Args:
        entries: The entries of the ledger.
        declarations: The taxes declared, and the last period declared of each.
        rates: The contribution rates, from each date on.
        today: The periods up to the one of this day are listed.

    Returns:
        The declarations, social contributions first then by period, and
        warnings about payments the ledger cannot account for.
    """
    entries = list(entries)
    paid = {entry.number: entry for entry in entries if entry.paid is not None}
    taxes: list[tuple[Tax, Declaration]] = [
        (tax, declaration)
        for tax, declaration in (
            ("urssaf", declarations.urssaf),
            ("vat", declarations.vat),
        )
        if declaration is not None
    ]
    result = []
    for tax, declaration in taxes:
        for start, end in _periods(declaration, today):
            period_paid = [
                entry
                for entry in paid.values()
                if entry.paid is not None and start <= entry.paid <= end
            ]
            result.append(
                Due(
                    tax=tax,
                    start=start,
                    end=end,
                    deadline=_deadline(end, declaration),
                    in_progress=end >= today,
                    invoices=tuple(sorted(entry.number for entry in period_paid)),
                    collected=round(sum(entry.amount for entry in period_paid), 2),
                    owed=_owed(tax, period_paid, rates),
                )
            )
    warnings = []
    # Credit notes have no payment date: one cancelling a paid invoice may have
    # been refunded, which the ledger cannot tell.
    first = min(
        (declaration.declared_through for _, declaration in taxes), default=None
    )
    for entry in entries:
        cancelled = paid.get(entry.cancels or "")
        if first is not None and cancelled is not None:
            assert cancelled.paid is not None
            if cancelled.paid > first:
                warnings.append(
                    f"{entry.number} cancels {cancelled.number}, paid on "
                    f"{cancelled.paid}: deduct any refund by hand"
                )
    for due in result:
        if due.owed is None:
            warnings.append(
                f"No contribution rate for the payments of {due.start:%Y-%m}"
            )
    return result, warnings
