"""Turn raw Google Calendar events into training sessions."""

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, NamedTuple

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    NonNegativeFloat,
    ValidationError,
)
from yaml import YAMLError, safe_load

from invoicez.gcal.diagnostics import Diagnostic
from invoicez.gcal.settings import DurationRules, GcalSettings, compile_title_pattern

_HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")
_SATURDAY = 5

Level = Literal["info", "warning", "error"]
Kind = Literal["day", "half_day"]


def _to_str(value: Any) -> Any:
    # YAML reads "ref: 12345" as an int and "invoice: 2020-11-02" as a date.
    return value if value is None else str(value)


_Str = Annotated[str | None, BeforeValidator(_to_str)]


class SessionDescription(BaseModel, extra="ignore", frozen=True):
    """The YAML mapping an event description may hold."""

    ref: _Str = None
    preparation_days: NonNegativeFloat = 0
    # Invoice number written by hand before invoicez tracked documents.
    legacy_invoice: _Str = Field(default=None, alias="invoice")


_KNOWN_KEYS = frozenset(
    field.alias or name for name, field in SessionDescription.model_fields.items()
)
# Payment tracking written by hand, not read by invoicez (out of scope, D4).
_UNTRACKED_KEYS = frozenset(("sent", "paid"))
_KEY_RE = re.compile(rf"\b({'|'.join(sorted(_KNOWN_KEYS))})\s*:")


class Session(BaseModel, extra="forbid", frozen=True):
    """One calendar event of a training: a block of days or a single half-day."""

    event_id: str
    html_link: str
    title: str
    continuation: bool
    # Values keep the case of the title; settings lookups ignore case.
    company: str
    training: str
    place: str
    extra: str | None
    start: date
    # Inclusive.
    end: date
    kind: Kind
    # Number of days or half-days, according to kind.
    quantity: int
    description: SessionDescription


class _Problem(NamedTuple):
    """A diagnostic about an event, before its id and title are attached."""

    level: Level
    message: str


class _Span(NamedTuple):
    start: date
    end: date
    kind: Kind
    quantity: int
    warnings: list[str]


def _all_day_span(first: date, after: date) -> _Span | _Problem:
    days = (after - first).days
    if days < 1:
        return _Problem("error", f"all-day event ends before it starts ({after})")
    weekend = [
        str(day)
        for day in (first + timedelta(days=i) for i in range(days))
        if day.weekday() >= _SATURDAY
    ]
    warnings = (
        [f"spans weekend days, billed as worked: {', '.join(weekend)}"]
        if weekend
        else []
    )
    return _Span(first, after - timedelta(days=1), "day", days, warnings)


def _timed_span(
    start: datetime, end: datetime, rules: DurationRules
) -> _Span | _Problem:
    if start.date() != end.date():
        return _Problem("warning", "timed event over several days, not billed")
    hours = (end - start).total_seconds() / 3600
    for kind, (low, high) in (
        ("half_day", rules.half_day_hours),
        ("day", rules.day_hours),
    ):
        if low <= hours <= high:
            return _Span(start.date(), start.date(), kind, 1, [])
    return _Problem(
        "warning",
        f"lasts {hours:g} h, neither a half-day {rules.half_day_hours} nor a "
        f"day {rules.day_hours}, not billed",
    )


def _span(raw: Mapping[str, Any], rules: DurationRules) -> _Span | _Problem:
    try:
        start, end = raw["start"], raw["end"]
        if "date" in start:
            return _all_day_span(
                date.fromisoformat(start["date"]), date.fromisoformat(end["date"])
            )
        return _timed_span(
            datetime.fromisoformat(start["dateTime"]),
            datetime.fromisoformat(end["dateTime"]),
            rules,
        )
    except (KeyError, TypeError, ValueError) as e:
        return _Problem("error", f"unreadable start or end ({e!r})")


def _description(
    raw: Mapping[str, Any],
) -> tuple[SessionDescription, list[_Problem]] | _Problem:
    # The parsed description, with notes about what was ignored.
    text = raw.get("description") or ""
    if not text.strip():
        return SessionDescription(), []
    if _HTML_RE.search(text):
        # Checked before YAML: "<p>ref: X</p>" is a YAML mapping too.
        if keys := sorted(set(_KEY_RE.findall(text))):
            return SessionDescription(), [
                _Problem(
                    "warning",
                    f"description is HTML, ignored, but mentions {', '.join(keys)}: "
                    "rewrite it as plain YAML",
                )
            ]
        return SessionDescription(), [_Problem("info", "description is HTML, ignored")]
    try:
        data = safe_load(text)
    except YAMLError:
        data = None
    if not isinstance(data, dict):
        return SessionDescription(), [
            _Problem("info", "description is free text, ignored")
        ]
    try:
        description = SessionDescription.model_validate(data)
    except ValidationError as e:
        errors = "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in e.errors()
        )
        return _Problem("error", f"invalid description ({errors})")
    unknown = sorted(map(str, data.keys() - _KNOWN_KEYS - _UNTRACKED_KEYS))
    notes = (
        [_Problem("warning", f"unknown description keys ignored: {', '.join(unknown)}")]
        if unknown
        else []
    )
    return description, notes


def _diagnostic(level: Level, raw: Mapping[str, Any], message: str) -> Diagnostic:
    return Diagnostic(
        level=level,
        event_id=raw.get("id"),
        message=f"{raw.get('summary', '')!r}: {message}",
    )


def parse_events(
    raw_events: Iterable[Mapping[str, Any]], settings: GcalSettings
) -> tuple[list[Session], list[Diagnostic]]:
    """Parse the events whose titles match the settings' title pattern.

    Events with other titles and cancelled events are skipped silently. Events
    that match but cannot be billed as they are yield a diagnostic instead of a
    session.

    Returns:
        The sessions, in event order, and the diagnostics.
    """
    pattern = compile_title_pattern(settings.title_pattern)
    sessions: list[Session] = []
    diagnostics: list[Diagnostic] = []
    for raw in raw_events:
        match = pattern.fullmatch(raw.get("summary") or "")
        if match is None or raw.get("status") == "cancelled":
            continue
        span = _span(raw, settings.durations)
        described = _description(raw)
        problems = [p for p in (span, described) if isinstance(p, _Problem)]
        diagnostics.extend(_diagnostic(p.level, raw, p.message) for p in problems)
        if isinstance(span, _Problem) or isinstance(described, _Problem):
            continue
        description, notes = described
        diagnostics.extend(_diagnostic("warning", raw, w) for w in span.warnings)
        diagnostics.extend(_diagnostic(n.level, raw, n.message) for n in notes)
        extra = match.groupdict().get("extra")
        sessions.append(
            Session(
                event_id=raw["id"],
                html_link=raw.get("htmlLink", ""),
                title=raw["summary"],
                continuation=match.group("continuation") is not None,
                company=match.group("company").strip(),
                training=match.group("training").strip(),
                place=match.group("place").strip(),
                extra=extra.strip() if extra else None,
                start=span.start,
                end=span.end,
                kind=span.kind,
                quantity=span.quantity,
                description=description,
            )
        )
    return sessions, diagnostics
