"""Estimate the social contributions and income tax due on invoices."""

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def to_euros(amount: float) -> int:
    # Rounded half away from zero, as spreadsheets and the administration do.
    return int(Decimal(str(amount)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def contribution_rate(rates: Mapping[date, float], day: date) -> float | None:
    """Return the contribution rate in force on a day.

    Returns:
        The rate of the latest date on or before day, or None if there is none.
    """
    started = [start for start in rates if start <= day]
    return rates[max(started)] if started else None


def income_tax(taxable: float, brackets: Mapping[float, float], shares: float) -> float:
    """Return the income tax due on a taxable income, by the progressive scale.

    The income is split into the household's shares, without the cap on the
    benefit of the quotient familial nor the décote.

    Args:
        taxable: Taxable income of the year.
        brackets: Rate applying above each threshold, per share.
        shares: Tax shares of the household.

    Returns:
        The tax, unrounded.
    """
    per_share = taxable / shares
    tax = 0.0
    previous_rate = 0.0
    for threshold, rate in sorted(brackets.items()):
        # Each threshold raises the rate of what lies above it.
        tax += (rate - previous_rate) * max(0.0, per_share - threshold)
        previous_rate = rate
    return tax * shares
