from datetime import date
from typing import Self

from pydantic import BaseModel, PositiveFloat, model_validator


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

    @model_validator(mode="after")
    def _check_spreadsheet(self) -> Self:
        if self.pivot_date is not None and self.spreadsheet is None:
            msg = "pivot_date needs a spreadsheet to read older invoices from"
            raise ValueError(msg)
        return self
