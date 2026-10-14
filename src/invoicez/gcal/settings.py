import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Self

from pydantic import BaseModel, model_validator

from invoicez.exceptions import InvoicezError

TITLE_PATTERN_REQUIRED_GROUPS = frozenset(
    ("continuation", "company", "training", "place")
)


def compile_title_pattern(title_pattern: str) -> re.Pattern[str]:
    # Titles must be matched with fullmatch: the pattern may lack ^ and $.
    return re.compile(title_pattern, re.VERBOSE)


class Rate(BaseModel, extra="forbid", frozen=True):
    # Half-days are billed as half a day at this price.
    day: float
    preparation_day: float | None = None


class Training(BaseModel, extra="forbid", frozen=True):
    name: str
    rate: str
    short_name: str | None = None


class DurationRules(BaseModel, extra="forbid", frozen=True):
    # Inclusive bounds, in hours, for a timed event to count as such.
    half_day_hours: tuple[float, float] = (2.5, 4.0)
    day_hours: tuple[float, float] = (6.0, 9.0)

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        for name, (low, high) in (
            ("half_day_hours", self.half_day_hours),
            ("day_hours", self.day_hours),
        ):
            if not 0 < low <= high:
                msg = f"{name} must be (low, high) with 0 < low <= high"
                raise ValueError(msg)
        if self.half_day_hours[1] >= self.day_hours[0]:
            msg = "half_day_hours must end before day_hours starts"
            raise ValueError(msg)
        return self


class Templates(BaseModel, extra="forbid", frozen=True):
    # Plain Jinja strings.
    description: str
    day_line: str
    half_day_line: str
    preparation_line: str


class GcalSettings(BaseModel, extra="forbid", frozen=True):
    calendar_id: str | None = None
    # Engagements ending before this date are ignored.
    start_date: date
    # Verbose regex with groups continuation, company, training, place and
    # optionally extra and tentative (unconfirmed bookings, e.g. "? Orsys - …").
    title_pattern: str
    # UN/ECE Rec 20 code, as BT-130 requires (BR-CL-23).
    unit: str = "DAY"
    output_dir: str = "gcal-ymls/{year}"
    durations: DurationRules = DurationRules()
    templates: Templates
    rates: dict[str, Rate]
    # Company key -> training token in event titles -> training.
    trainings: dict[str, dict[str, Training]]

    @model_validator(mode="after")
    def _check_title_pattern(self) -> Self:
        try:
            pattern = compile_title_pattern(self.title_pattern)
        except re.error as e:
            msg = f"title_pattern does not compile: {e}"
            raise ValueError(msg) from e
        missing = TITLE_PATTERN_REQUIRED_GROUPS - pattern.groupindex.keys()
        if missing:
            msg = f"title_pattern lacks the groups {', '.join(sorted(missing))}"
            raise ValueError(msg)
        return self

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
