"""Anonymise raw Google Calendar events into a shareable test fixture.

Pure functions, no Google imports: scripts/dump_gcal_fixture.py fetches the
events and writes what these functions return.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from yaml import YAMLError, safe_dump, safe_load

FREE_TEXT = "Free text"
HTML_TEXT = "<p>Free text</p>"

# Description keys whose values are our own data (invoice numbers, dates,
# counts), not the client's.
_KEPT_DESCRIPTION_KEYS = frozenset(("invoice", "paid", "sent", "preparation_days"))
_EVENT_TIME_KEYS = ("date", "dateTime", "timeZone")
# Places that say how a session is given rather than where: kept, as billing
# may depend on them. Others could point to a client and are pseudonymised.
_KEPT_PLACES = frozenset(("cv",))
# Non-matching titles kept in the fixture: two segments, the shape of titles
# that may have been meant to match (decision D2). Others are personal events.
_FIXTURE_OTHER_SEGMENTS = 2
_HTML_RE = re.compile(r"<[a-zA-Z/][^>]*>")
_SCALARS = (int, float, bool, date, type(None))
# Shorter real values (e.g. "cv") are too likely to appear by chance.
_MIN_LEAK_LENGTH = 4
# Real values shaped like pseudonyms, when re-anonymising a fixture.
_PSEUDONYM_RE = re.compile(
    r"(company|training|place|extra|ref|id|htmllink|other|value)_\d+"
)


def compile_title_pattern(title_pattern: str) -> re.Pattern[str]:
    return re.compile(title_pattern, re.VERBOSE)


def _with_case_shape(pseudonym: str, real: str) -> str:
    if real.isupper():
        return pseudonym.upper()
    if real.islower():
        return pseudonym.lower()
    return pseudonym


class Pseudonyms:
    """Consistent pseudonyms, numbered per category in order of appearance.

    Text values are identified case-insensitively and their pseudonyms keep
    the case shape of each occurrence: "BDA" -> "TRAINING_0", "bda" ->
    "training_0". Identifiers (ids, links) are kept exact.
    """

    def __init__(self) -> None:
        self._numbers: dict[tuple[str, str, str], int] = {}
        self._next: Counter[tuple[str, str]] = Counter()
        self.mapping: dict[str, str] = {}

    def get(
        self, category: str, real: str, *, scope: str = "", text: bool = True
    ) -> str:
        key = (category, scope, real.casefold() if text else real)
        if key not in self._numbers:
            self._numbers[key] = self._next[category, scope]
            self._next[category, scope] += 1
        pseudonym = f"{category}_{self._numbers[key]}"
        if text:
            pseudonym = _with_case_shape(pseudonym, real)
        label = f"{self.base('Company', scope)}/{pseudonym}" if scope else pseudonym
        self.mapping[label] = real
        return pseudonym

    def base(self, category: str, real: str) -> str:
        return f"{category}_{self._numbers[category, '', real.casefold()]}"

    def knows(self, category: str, real: str, *, scope: str = "") -> bool:
        return (category, scope, real.casefold()) in self._numbers

    def entries(self, category: str) -> list[tuple[str, str, int]]:
        """Return (scope, casefolded real value, number) of a category."""
        return [
            (scope, real, number)
            for (entry_category, scope, real), number in self._numbers.items()
            if entry_category == category
        ]


def _anonymize_title(match: re.Match[str], pseudonyms: Pseudonyms) -> str:
    company = match["company"]
    company_pseudonym = pseudonyms.get("Company", company)
    training = pseudonyms.get("Training", match["training"], scope=company.casefold())
    place = match["place"]
    if place.casefold() not in _KEPT_PLACES:
        place = pseudonyms.get("Place", place)
    title = f"{company_pseudonym} - {training} - {place}"
    if extra := match.groupdict().get("extra"):
        title += f" - {pseudonyms.get('Extra', extra)}"
    return f"-> {title}" if match["continuation"] else title


def _anonymize_other_title(summary: str, pseudonyms: Pseudonyms) -> str:
    # Keep the title's shape, and known companies and trainings recognisable,
    # to tell whether non-matching events should have matched (decision D2).
    segments = summary.split(" - ")
    company = segments[0] if pseudonyms.knows("Company", segments[0]) else None
    anonymized = []
    for segment in segments:
        if company is not None and segment.casefold() == company.casefold():
            anonymized.append(pseudonyms.get("Company", segment))
        elif company is not None and pseudonyms.knows(
            "Training", segment, scope=company.casefold()
        ):
            anonymized.append(
                pseudonyms.get("Training", segment, scope=company.casefold())
            )
        else:
            anonymized.append(pseudonyms.get("Other", segment))
    return " - ".join(anonymized)


def _anonymize_value(key: Any, value: Any, pseudonyms: Pseudonyms) -> Any:
    if key in _KEPT_DESCRIPTION_KEYS:
        return value
    if key == "ref":
        return pseudonyms.get("Ref", str(value))
    if isinstance(value, _SCALARS):
        return value
    return pseudonyms.get("Value", str(value))


def _anonymize_description(description: str, pseudonyms: Pseudonyms) -> str:
    if not description.strip():
        return description
    try:
        data = safe_load(description)
    except YAMLError:
        data = None
    if isinstance(data, dict):
        return safe_dump(
            {k: _anonymize_value(k, v, pseudonyms) for k, v in data.items()},
            allow_unicode=True,
            sort_keys=False,
        )
    return HTML_TEXT if _HTML_RE.search(description) else FREE_TEXT


def _anonymize_common(
    event: Mapping[str, Any], pseudonyms: Pseudonyms
) -> dict[str, Any]:
    anonymized: dict[str, Any] = {}
    for key in ("id", "recurringEventId"):
        if key in event:
            anonymized[key] = pseudonyms.get("Id", event[key], text=False)
    if "htmlLink" in event:
        anonymized["htmlLink"] = pseudonyms.get(
            "HTMLLink", event["htmlLink"], text=False
        )
    if "status" in event:
        anonymized["status"] = event["status"]
    for key in ("start", "end"):
        if key in event:
            anonymized[key] = {
                k: v for k, v in event[key].items() if k in _EVENT_TIME_KEYS
            }
    return anonymized


def anonymize(
    events: Sequence[Mapping[str, Any]], title_pattern: str
) -> tuple[list[dict[str, Any]], Pseudonyms]:
    """Anonymise raw API events, keeping what parsing and grouping need.

    Matching titles are rebuilt from pseudonymised groups (continuation and
    the "CV" place kept); other titles keep their " - " shape only and lose
    their description. YAML descriptions keep their keys, with `ref` and
    unknown string values pseudonymised; free text and HTML become
    placeholders. Only ids, links, status, start and end are kept otherwise.

    Returns:
        The anonymised events, in the same order, and the pseudonyms used
        (whose mapping must stay private).
    """
    pattern = compile_title_pattern(title_pattern)
    pseudonyms = Pseudonyms()
    matches = [pattern.fullmatch(event.get("summary", "")) for event in events]

    anonymized_events = []
    for event, match in zip(events, matches, strict=True):
        anonymized = _anonymize_common(event, pseudonyms)
        if match is not None:
            anonymized["summary"] = _anonymize_title(match, pseudonyms)
            if "description" in event:
                anonymized["description"] = _anonymize_description(
                    event["description"], pseudonyms
                )
        anonymized_events.append((event, anonymized))
    # Non-matching titles last, so they can refer to every known company.
    for event, anonymized in anonymized_events:
        if "summary" not in anonymized and "summary" in event:
            anonymized["summary"] = _anonymize_other_title(event["summary"], pseudonyms)
    return [anonymized for _, anonymized in anonymized_events], pseudonyms


def select_fixture_events(
    anonymized_events: Sequence[Mapping[str, Any]], title_pattern: str
) -> list[Mapping[str, Any]]:
    """Keep the events worth committing: matching titles and two-segment ones.

    Recurring instances of two-segment titles are left out: their dates are
    personal (a yearly series gives away a birth date).

    Returns:
        The selected events, in the same order.
    """
    pattern = compile_title_pattern(title_pattern)
    return [
        event
        for event in anonymized_events
        if pattern.fullmatch(summary := event.get("summary", ""))
        or (
            len(summary.split(" - ")) == _FIXTURE_OTHER_SEGMENTS
            and "recurringEventId" not in event
        )
    ]


def find_leaks(
    anonymized_events: Sequence[Mapping[str, Any]], mapping: Mapping[str, str]
) -> list[str]:
    """Return the real values still present in the anonymised titles and descriptions.

    Only these carry free text: ids and links are always replaced, and time
    zones (e.g. "Europe/Paris") would flag places.
    """
    dumped = "\n".join(
        str(event.get(key, ""))
        for event in anonymized_events
        for key in ("summary", "description")
    ).casefold()
    return sorted(
        {
            real
            for real in mapping.values()
            if len(real) >= _MIN_LEAK_LENGTH
            and not _PSEUDONYM_RE.fullmatch(real.casefold())
            and real.casefold() in dumped
        }
    )


def anonymized_settings(
    gcal_section: Mapping[str, Any], pseudonyms: Pseudonyms
) -> tuple[dict[str, Any], list[str]]:
    """Build a gcal settings section for the pseudonymised companies and trainings.

    Trainings take the rate of the real training with the same company and
    token (compared case-insensitively), else "TODO".

    Returns:
        The section, and the pseudonymised trainings left with a TODO rate.
    """
    real_trainings = {
        company.casefold(): {token.casefold(): t for token, t in trainings.items()}
        for company, trainings in (gcal_section.get("trainings") or {}).items()
    }
    trainings: dict[str, dict[str, Any]] = {}
    unknown = []
    for scope, real, number in sorted(
        pseudonyms.entries("Training"),
        key=lambda entry: (pseudonyms.base("Company", entry[0]), entry[2]),
    ):
        company = pseudonyms.base("Company", scope)
        real_training = real_trainings.get(scope, {}).get(real)
        if real_training is None:
            unknown.append(f"{company}/Training_{number}")
        trainings.setdefault(company.lower(), {})[f"training_{number}"] = {
            "name": f"Training {number} of {company}",
            "rate": real_training["rate"] if real_training else "TODO",
        }
    section = {
        key: gcal_section[key]
        for key in (
            "start_date",
            "title_pattern",
            "unit",
            "durations",
            "templates",
            "rates",
        )
        if key in gcal_section
    }
    return {**section, "trainings": trainings}, unknown


def _event_kind(event: Mapping[str, Any]) -> str:
    return "all-day" if "date" in event.get("start", {}) else "timed"


def _event_length(event: Mapping[str, Any]) -> str:
    start, end = event["start"], event["end"]
    if "date" in start:
        days = (
            date.fromisoformat(end["date"]) - date.fromisoformat(start["date"])
        ).days
        return f"{days} day(s)"
    seconds = (
        datetime.fromisoformat(end["dateTime"])
        - datetime.fromisoformat(start["dateTime"])
    ).total_seconds()
    return f"{seconds / 3600:g} h"


def _description_kind(description: str) -> str:
    if not description.strip():
        return "empty"
    if description in (FREE_TEXT, HTML_TEXT):
        return "free text" if description == FREE_TEXT else "HTML"
    return "YAML"


def _case_shape(value: str) -> str:
    if value.isupper():
        return "upper"
    if value.islower():
        return "lower"
    return "mixed"


def _counts(values: Iterable[Any]) -> str:
    return ", ".join(f"{key}: {n}" for key, n in sorted(Counter(values).items()))


def summarize(
    anonymized_events: Sequence[Mapping[str, Any]], title_pattern: str
) -> str:
    """Count what phase 2 relies on, from the anonymised events only.

    Returns:
        A plain text report, one count per line.
    """
    pattern = compile_title_pattern(title_pattern)
    matching: list[tuple[Mapping[str, Any], re.Match[str]]] = []
    other_segments: list[int] = []
    for event in anonymized_events:
        summary = event.get("summary", "")
        if match := pattern.fullmatch(summary):
            matching.append((event, match))
        else:
            other_segments.append(len(summary.split(" - ")))

    events = [event for event, _ in matching]
    groups = [match for _, match in matching]
    descriptions = [event.get("description", "") for event in events]
    yaml_keys = [
        str(key)
        for description in descriptions
        if _description_kind(description) == "YAML"
        for key in safe_load(description)
    ]
    pairs = {(m["company"].casefold(), m["training"].casefold()) for m in groups}
    lines = [
        f"events: {len(anonymized_events)}",
        f"status: {_counts(e.get('status', 'none') for e in anonymized_events)}",
        "recurring instances: "
        f"{sum('recurringEventId' in e for e in anonymized_events)}",
        f"non-matching titles: {len(other_segments)}",
        f"  segments: {_counts(other_segments)}",
        f"matching titles: {len(matching)}",
        f"  kinds: {_counts(map(_event_kind, events))}",
        f"  lengths: {_counts(map(_event_length, events))}",
        f"  time zones: {_counts(e['start'].get('timeZone', '-') for e in events)}",
        f"  continuations: {sum(m['continuation'] is not None for m in groups)}",
        f"  companies: {len({m['company'].casefold() for m in groups})}",
        f"  (company, training) pairs: {len(pairs)}",
        f"  training token case: {_counts(_case_shape(m['training']) for m in groups)}",
        f"  descriptions: {_counts(map(_description_kind, descriptions))}",
        f"  YAML description keys: {_counts(yaml_keys)}",
    ]
    return "\n".join(lines) + "\n"
