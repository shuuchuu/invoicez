from collections.abc import Mapping
from datetime import date
from typing import Any, Self

from pydantic import BaseModel, model_validator
from schedz.settings import CalendarSettings

from invoicez.exceptions import InvoicezError


class Rate(BaseModel, extra="forbid", frozen=True):
    # Half-days are billed as half a day at this price.
    day: float
    preparation_day: float | None = None


class Training(BaseModel, extra="forbid", frozen=True):
    name: str
    rate: str
    short_name: str | None = None


class Templates(BaseModel, extra="forbid", frozen=True):
    # Plain Jinja strings.
    description: str
    day_line: str
    half_day_line: str
    preparation_line: str
    expenses_day_line: str
    expenses_lump_sum_line: str


class GcalSettings(CalendarSettings, extra="forbid", frozen=True):
    """schedz's calendar settings (calendar_id, title_pattern, durations), billing."""

    # Engagements ending before this date are ignored.
    start_date: date
    # UN/ECE Rec 20 code, as BT-130 requires (BR-CL-23).
    unit: str = "DAY"
    output_dir: str = "gcal-ymls/{year}"
    templates: Templates
    rates: dict[str, Rate]
    # Company key -> training token in event titles -> training.
    trainings: dict[str, dict[str, Training]]

    @model_validator(mode="after")
    def _check_rates(self) -> Self:
        unknown = sorted(
            f"{company}.{token}: {training.rate!r}"
            for company, trainings in self.trainings.items()
            for token, training in trainings.items()
            if training.rate not in self.rates
        )
        if unknown:
            msg = f"trainings with an unknown rate: {', '.join(unknown)}"
            raise ValueError(msg)
        return self


def check_against_variables(
    settings: GcalSettings, global_mapping: Mapping[str, Any]
) -> None:
    """Check that the gcal settings only refer to companies of variables.yml.

    Raises:
        InvoicezError: If trainings are listed under unknown companies.
    """
    unknown = sorted(settings.trainings.keys() - global_mapping["companies"].keys())
    if unknown:
        msg = (
            f"gcal.trainings lists companies missing from variables.yml: "
            f"{', '.join(unknown)}"
        )
        raise InvoicezError(msg)
