from datetime import datetime
from re import VERBOSE, Pattern
from re import compile as re_compile
from typing import Annotated, Any, Optional

from pydantic import BaseModel, StringConstraints

from invoicez.exceptions import InvoicezError

from .duration import Duration, DurationUnit

title_pattern_required_fields = frozenset(
    {"continuation", "company", "training", "extra"}
)


StrippedLowercaseStr = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True)
]


class Title(BaseModel):
    continuation: bool
    company: StrippedLowercaseStr
    training: StrippedLowercaseStr
    place: StrippedLowercaseStr
    extra: StrippedLowercaseStr | None


class Event(BaseModel):
    model_config = {"use_enum_values": True}
    continuation: bool
    company: StrippedLowercaseStr
    training: StrippedLowercaseStr
    place: StrippedLowercaseStr
    link: str
    id: str
    start: datetime
    duration: Duration
    extra: StrippedLowercaseStr | None
    description: StrippedLowercaseStr | None = None

    @classmethod
    def from_gcal_event(
        cls, gcal_event: dict[str, Any], title_pattern: Pattern
    ) -> Optional["Event"]:
        parsed_title = cls.parse_title(gcal_event["summary"], title_pattern)
        if parsed_title is None:
            return None
        start, duration = cls._process_dates(gcal_event)
        return cls(
            continuation=parsed_title.continuation,
            company=parsed_title.company,
            training=parsed_title.training,
            place=parsed_title.place,
            extra=parsed_title.extra,
            start=start,
            duration=duration,
            description=gcal_event.get("description"),
            link=gcal_event["htmlLink"],
            id=gcal_event["id"],
        )

    @staticmethod
    def compile_pattern(pattern: str) -> Pattern:
        compiled_pattern = re_compile(pattern, VERBOSE)
        missing_fields = title_pattern_required_fields.difference(
            compiled_pattern.groupindex.keys()
        )

        if missing_fields:
            msg = (
                f"Title pattern doesn't contain the necessary fields {missing_fields}."
            )
            raise InvoicezError(msg)
        return compiled_pattern

    @staticmethod
    def parse_title(title: str, pattern: Pattern) -> Title | None:
        match = pattern.match(title)
        if match is None:
            return None

        continuation = match.group("continuation") is not None
        company = match.group("company")
        training = match.group("training")
        place = match.group("place")
        extra = match.group("extra")
        return Title(
            continuation=continuation,
            company=company,
            training=training,
            place=place,
            extra=extra,
        )

    @staticmethod
    def _process_dates(gcal_event: dict[str, Any]) -> tuple[datetime, Duration]:
        next(iter(gcal_event["start"].values()))
        key = "dateTime" if "dateTime" in gcal_event["start"] else "date"
        start_datetime = datetime.fromisoformat(gcal_event["start"][key])
        end_datetime = datetime.fromisoformat(gcal_event["end"][key])
        delta = end_datetime - start_datetime
        seconds_in_an_hour = 60 * 60
        seconds_in_a_day = seconds_in_an_hour * 24
        if delta.seconds % seconds_in_a_day == 0:
            duration = Duration(
                n=delta.seconds // seconds_in_a_day, unit=DurationUnit.day
            )
        elif delta.seconds % (seconds_in_an_hour * 3) == 0:
            duration = Duration(
                n=delta.seconds // (seconds_in_an_hour * 3), unit=DurationUnit.half_day
            )
        else:
            msg = (
                "No support for durations that are not full days or 3 hours (half a "
                f"day). Event: {gcal_event}"
            )
            raise InvoicezError(msg)
        return start_datetime.replace(tzinfo=None), duration
