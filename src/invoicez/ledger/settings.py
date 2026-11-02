from datetime import date, timedelta
from typing import Literal, Self

from pydantic import BaseModel, Field, PositiveFloat, model_validator

Period = Literal["monthly", "quarterly"]


def period_start(day: date, period: Period) -> date:
    # First day of the calendar month or quarter of a day.
    month = day.month if period == "monthly" else (day.month - 1) // 3 * 3 + 1
    return date(day.year, month, 1)


def next_period(start: date, period: Period) -> date:
    # First day of the period after the one starting on a day.
    month = start.month - 1 + (1 if period == "monthly" else 3)
    return date(start.year + month // 12, month % 12 + 1, 1)


class Declaration(BaseModel, extra="forbid", frozen=True):
    """A tax declared periodically on the payments received in each period."""

    period: Period = "monthly"
    # Last day of the last period declared: the later ones are owed.
    declared_through: date
    # Day of the month after a period its declaration is due by; None for the
    # last day.
    due_day: int | None = Field(default=None, ge=1, le=28)

    @model_validator(mode="after")
    def _check_declared_through(self) -> Self:
        day = self.declared_through + timedelta(days=1)
        if period_start(day, self.period) != day:
            msg = f"declared_through must be the last day of a {self.period} period"
            raise ValueError(msg)
        return self


class Declarations(BaseModel, extra="forbid", frozen=True):
    # Social contributions (URSSAF), on the payments excluding VAT, at the rate
    # of contribution_rates in force on the day of each payment.
    urssaf: Declaration | None = None
    # VAT, due when payments are received (services).
    vat: Declaration | None = None


class IncomeTax(BaseModel, extra="forbid", frozen=True):
    # Share of the turnover deducted before tax (34 % for micro-BNC).
    allowance: float = 0.34
    # Tax shares of the household (quotient familial), its other income ignored.
    shares: PositiveFloat = 1
    # By year of income: the rate applying above each threshold, per share.
    brackets: dict[int, dict[float, float]] = {}


class LedgerSettings(BaseModel, extra="forbid", frozen=True):
    # Invoices dated before this date are read from the spreadsheet, the others
    # from the documents and SuperPDP. None reads everything from the latter.
    pivot_date: date | None = None
    # Spreadsheet with one sheet per year, relative to the repository.
    spreadsheet: str | None = None
    # Lines whose description matches are expenses passed on to the client.
    expense_pattern: str = "frais"
    # Lines in days whose description matches are training days.
    training_day_pattern: str = r"^Jour(née)? de (formation|certification)"
    # Social contributions, as a share of the turnover, from each date on.
    contribution_rates: dict[date, float] = {}
    income_tax: IncomeTax | None = None
    declarations: Declarations = Declarations()

    @model_validator(mode="after")
    def _check_spreadsheet(self) -> Self:
        if self.pivot_date is not None and self.spreadsheet is None:
            msg = "pivot_date needs a spreadsheet to read older invoices from"
            raise ValueError(msg)
        return self
