from collections import Counter
from datetime import date
from typing import Any

from invoicez.gcal.parsing import SessionDescription, parse_events
from invoicez.gcal.settings import GcalSettings


def _event(
    summary: str, start: dict[str, str], end: dict[str, str], **extra: Any
) -> dict[str, Any]:
    return {
        "id": "event-id",
        "htmlLink": "https://calendar.google.com/event?eid=event-id",
        "status": "confirmed",
        "summary": summary,
        "start": start,
        "end": end,
        **extra,
    }


def _all_day(summary: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return _event(summary, {"date": start}, {"date": end}, **extra)


def _timed(
    summary: str, day: str, start: str, end: str, **extra: Any
) -> dict[str, Any]:
    return _event(
        summary,
        {"dateTime": f"{day}T{start}:00+02:00", "timeZone": "Europe/Paris"},
        {"dateTime": f"{day}T{end}:00+02:00", "timeZone": "Europe/Paris"},
        **extra,
    )


def test_all_day_event(gcal_settings: GcalSettings) -> None:
    # origin/gcal read every all-day event as 0 days.
    sessions, diagnostics = parse_events(
        [_all_day("Orsys - BDA - cv", "2026-10-05", "2026-10-08")], gcal_settings
    )

    assert diagnostics == []
    [session] = sessions
    assert (session.start, session.end) == (date(2026, 10, 5), date(2026, 10, 7))
    assert (session.kind, session.quantity) == ("day", 3)
    assert session.event_id == "event-id"
    assert session.html_link.endswith("eid=event-id")
    assert session.description == SessionDescription()


def test_timed_day(gcal_settings: GcalSettings) -> None:
    # origin/gcal rejected anything but multiples of 3 hours.
    sessions, diagnostics = parse_events(
        [_timed("Orsys - BDA - cv", "2026-10-05", "09:00", "17:00")], gcal_settings
    )

    assert diagnostics == []
    [session] = sessions
    assert (session.start, session.end) == (date(2026, 10, 5), date(2026, 10, 5))
    assert (session.kind, session.quantity) == ("day", 1)


def test_timed_half_day(gcal_settings: GcalSettings) -> None:
    sessions, diagnostics = parse_events(
        [_timed("Orsys - BDA - cv", "2026-10-05", "09:00", "12:30")], gcal_settings
    )

    assert diagnostics == []
    assert [(s.kind, s.quantity) for s in sessions] == [("half_day", 1)]


def test_values_keep_their_case(gcal_settings: GcalSettings) -> None:
    # origin/gcal lowercased everything, including refs.
    event = _all_day(
        "Orsys - bda - CV", "2026-10-05", "2026-10-06", description="ref: BDAzz1"
    )

    [session], _ = parse_events([event], gcal_settings)

    assert (session.company, session.training, session.place) == ("Orsys", "bda", "CV")
    assert session.description.ref == "BDAzz1"


def test_title_groups(gcal_settings: GcalSettings) -> None:
    # The pattern has no anchors and a lazy place: match() left place empty.
    events = [
        _all_day("Orsys - BDA - cv - Session Alpha", "2026-10-05", "2026-10-06"),
        _all_day("  -> Sparks Formation - PAV - Nantes ", "2026-10-12", "2026-10-13"),
        # Unconfirmed bookings.
        _all_day("? Orsys - PYA - CV - Intra Acme", "2026-11-23", "2026-11-24"),
        _all_day("? -> Orsys - PYA - CV - Intra Acme", "2026-11-30", "2026-12-01"),
    ]

    sessions, _ = parse_events(events, gcal_settings)

    assert [
        (s.tentative, s.continuation, s.company, s.training, s.place, s.extra)
        for s in sessions
    ] == [
        (False, False, "Orsys", "BDA", "cv", "Session Alpha"),
        (False, True, "Sparks Formation", "PAV", "Nantes", None),
        (True, False, "Orsys", "PYA", "CV", "Intra Acme"),
        (True, True, "Orsys", "PYA", "CV", "Intra Acme"),
    ]


def test_skipped_silently(gcal_settings: GcalSettings) -> None:
    events = [
        _all_day("Dentist", "2026-10-05", "2026-10-06"),
        _all_day("Orsys - BDA", "2026-10-05", "2026-10-06"),
        _all_day("Orsys - BDA - cv", "2026-10-05", "2026-10-06", status="cancelled"),
        {"id": "no-summary", "status": "confirmed"},
    ]

    assert parse_events(events, gcal_settings) == ([], [])


def test_short_and_long_timed_events(gcal_settings: GcalSettings) -> None:
    events = [
        _timed("Orsys - BDA - cv", "2026-10-05", "09:00", "09:30"),
        _timed("Orsys - BDA - cv", "2026-10-05", "09:00", "14:00"),
        _timed("Orsys - BDA - cv", "2026-10-05", "08:00", "19:00"),
    ]

    sessions, diagnostics = parse_events(events, gcal_settings)

    assert sessions == []
    assert [d.level for d in diagnostics] == ["warning"] * 3
    assert "lasts 0.5 h" in diagnostics[0].message
    assert "lasts 5 h" in diagnostics[1].message
    assert "lasts 11 h" in diagnostics[2].message


def test_timed_event_over_several_days(gcal_settings: GcalSettings) -> None:
    event = _event(
        "Orsys - BDA - cv",
        {"dateTime": "2026-10-05T22:00:00+02:00"},
        {"dateTime": "2026-10-06T02:00:00+02:00"},
    )

    sessions, [diagnostic] = parse_events([event], gcal_settings)

    assert sessions == []
    assert diagnostic.level == "warning"
    assert "several days" in diagnostic.message


def test_weekend_days_are_billed_with_a_warning(gcal_settings: GcalSettings) -> None:
    # Friday to Monday.
    event = _all_day("Orsys - BDA - cv", "2026-10-09", "2026-10-13")

    [session], [diagnostic] = parse_events([event], gcal_settings)

    assert session.quantity == 4
    assert diagnostic.level == "warning"
    assert diagnostic.event_id == "event-id"
    assert diagnostic.message == (
        "'Orsys - BDA - cv': spans weekend days, billed as worked: "
        "2026-10-10, 2026-10-11"
    )


def test_unreadable_dates(gcal_settings: GcalSettings) -> None:
    events = [
        _all_day("Orsys - BDA - cv", "2026-10-05", "2026-10-05"),
        _all_day("Orsys - BDA - cv", "not a date", "2026-10-06"),
        _event("Orsys - BDA - cv", {}, {}),
    ]

    sessions, diagnostics = parse_events(events, gcal_settings)

    assert sessions == []
    assert [d.level for d in diagnostics] == ["error"] * 3


def test_descriptions(gcal_settings: GcalSettings) -> None:
    def parse(description: str) -> tuple[Any, list[tuple[str, str]]]:
        event = _all_day(
            "Orsys - BDA - cv", "2026-10-05", "2026-10-06", description=description
        )
        sessions, diagnostics = parse_events([event], gcal_settings)
        return (
            sessions[0].description if sessions else None,
            [(d.level, d.message.split(": ", 1)[1]) for d in diagnostics],
        )

    assert parse("ref: 12345\ninvoice: 2020-11-02\npaid: 2020-12-30") == (
        SessionDescription(ref="12345", invoice="2020-11-02"),
        [],
    )
    assert parse("ref: X\npreparation_days: 1.5") == (
        SessionDescription(ref="X", preparation_days=1.5),
        [],
    )
    assert parse("ref: X\ntrainees: 8") == (SessionDescription(ref="X", trainees=8), [])
    assert parse("<p>Call the client first</p>") == (
        SessionDescription(),
        [("info", "description is HTML, ignored")],
    )
    # Would parse as YAML, with a "<p>ref" key.
    assert parse("<p>ref: X<br>preparation_days: 1</p>") == (
        SessionDescription(),
        [
            (
                "warning",
                "description is HTML, ignored, but mentions preparation_days, ref: "
                "rewrite it as plain YAML",
            )
        ],
    )
    # A typo must not silently drop preparation days.
    assert parse("ref: X\npreparation_day: 1\nsent: 2020-12-01") == (
        SessionDescription(ref="X"),
        [("warning", "unknown description keys ignored: preparation_day")],
    )
    assert parse("Call the client first") == (
        SessionDescription(),
        [("info", "description is free text, ignored")],
    )
    description, diagnostics = parse("ref: X\npreparation_days: lots")
    assert description is None
    assert diagnostics[0][0] == "error"
    assert diagnostics[0][1].startswith("invalid description (preparation_days: ")


def test_fixture(raw_events: list[dict[str, Any]], gcal_settings: GcalSettings) -> None:
    sessions, diagnostics = parse_events(raw_events, gcal_settings)

    # 194 matching events, of which 2 are too short to bill.
    assert len(sessions) == 192
    assert Counter((d.level, d.message.split(": ", 1)[1][:9]) for d in diagnostics) == {
        ("warning", "lasts 0.5"): 1,
        ("warning", "lasts 0.7"): 1,
        # The two short events hold the HTML descriptions not counted here.
        ("info", "descripti"): 2,
    }
    assert Counter(s.kind for s in sessions) == {"day": 160, "half_day": 32}
    assert sum(s.quantity for s in sessions if s.kind == "day") == 446
    assert sum(s.continuation for s in sessions) == 35
    assert sum(s.description.ref is not None for s in sessions) == 33
    assert sum(s.description.preparation_days for s in sessions) == 3
