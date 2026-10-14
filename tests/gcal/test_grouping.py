from collections import Counter
from datetime import date, timedelta
from typing import Any

from invoicez.gcal.grouping import group_sessions
from invoicez.gcal.parsing import Session, SessionDescription, parse_events
from invoicez.gcal.settings import GcalSettings


def _session(
    day: int,
    *,
    continuation: bool = False,
    company: str = "Orsys",
    training: str = "BDA",
    place: str = "cv",
    extra: str | None = None,
    days: int = 1,
    description: SessionDescription | None = None,
) -> Session:
    start = date(2026, 10, 1) + timedelta(days=day)
    return Session(
        event_id=f"id_{day}",
        html_link=f"link_{day}",
        title=f"{'-> ' if continuation else ''}{company} - {training} - {place}",
        continuation=continuation,
        company=company,
        training=training,
        place=place,
        extra=extra,
        start=start,
        end=start + timedelta(days=days - 1),
        kind="day",
        quantity=days,
        description=description or SessionDescription(),
    )


def _ids(engagements: Any) -> list[list[str]]:
    return [[s.event_id for s in e.sessions] for e in engagements]


def test_continuation_without_root() -> None:
    engagements, [diagnostic] = group_sessions([_session(0, continuation=True)])

    assert engagements == []
    assert diagnostic.level == "error"
    assert diagnostic.event_id == "id_0"
    assert (
        diagnostic.message == "'-> Orsys - BDA - cv': continuation without a root event"
    )


def test_root_after_continuation() -> None:
    # Only the orphan is reported; the later root is still billed.
    engagements, diagnostics = group_sessions(
        [_session(0, continuation=True), _session(1)]
    )

    assert _ids(engagements) == [["id_1"]]
    assert [(d.level, d.event_id) for d in diagnostics] == [("error", "id_0")]


def test_continuations() -> None:
    sessions = [_session(0, days=3)] + [
        _session(day, continuation=True) for day in (7, 8, 14)
    ]

    [engagement], diagnostics = group_sessions(sessions)

    assert diagnostics == []
    assert _ids([engagement]) == [["id_0", "id_7", "id_8", "id_14"]]
    assert engagement.id == "id_0"
    assert (engagement.company, engagement.training, engagement.place) == (
        "Orsys",
        "BDA",
        "cv",
    )
    assert (engagement.start, engagement.end) == (date(2026, 10, 1), date(2026, 10, 15))


def test_sessions_are_sorted() -> None:
    engagements, diagnostics = group_sessions(
        [_session(7, continuation=True), _session(0), _session(3, training="PAV")]
    )

    assert diagnostics == []
    assert _ids(engagements) == [["id_0", "id_7"], ["id_3"]]


def test_a_new_root_closes_the_previous_engagement() -> None:
    sessions = [
        _session(0),
        _session(1, continuation=True),
        _session(7),
        _session(8, continuation=True),
    ]

    engagements, _ = group_sessions(sessions)

    assert _ids(engagements) == [["id_0", "id_1"], ["id_7", "id_8"]]


def test_key_is_the_whole_title_ignoring_case() -> None:
    sessions = [
        _session(0),
        _session(1, place="Paris"),
        _session(2, extra="Session Alpha"),
        _session(3, company="Sparks"),
        _session(4, continuation=True, company="ORSYS", training="bda", place="CV"),
        _session(5, continuation=True, place="paris"),
        _session(6, continuation=True, extra="session alpha"),
    ]

    engagements, diagnostics = group_sessions(sessions)

    assert diagnostics == []
    assert _ids(engagements) == [
        ["id_0", "id_4"],
        ["id_1", "id_5"],
        ["id_2", "id_6"],
        ["id_3"],
    ]
    # The root's values, whatever the case of the continuations.
    assert engagements[0].training == "BDA"


def test_gap() -> None:
    sessions = [
        _session(0),
        _session(31, continuation=True),
        _session(64, continuation=True),
    ]

    [engagement], diagnostics = group_sessions(sessions)

    assert len(engagement.sessions) == 3
    # 31 days between the end of a session and the start of the next is fine.
    assert [(d.level, d.event_id) for d in diagnostics] == [("warning", "id_64")]
    assert "starts 33 days after the previous session" in diagnostics[0].message


def test_continuation_description_is_ignored_with_a_warning() -> None:
    sessions = [
        _session(0, description=SessionDescription(ref="REF")),
        _session(1, continuation=True, description=SessionDescription(ref="OTHER")),
    ]

    [engagement], [diagnostic] = group_sessions(sessions)

    assert engagement.description.ref == "REF"
    assert diagnostic.level == "warning"
    assert diagnostic.event_id == "id_1"


def test_fingerprint() -> None:
    def fingerprint(*sessions: Session) -> str:
        [engagement], _ = group_sessions(sessions)
        return engagement.fingerprint

    reference = fingerprint(_session(0), _session(7, continuation=True))

    assert len(reference) == 64
    assert reference == fingerprint(
        _session(0, company="ORSYS", training="bda", place="Paris"),
        _session(7, continuation=True, company="ORSYS", training="bda", place="Paris"),
    )
    assert reference != fingerprint(_session(0))
    assert reference != fingerprint(_session(0), _session(8, continuation=True))
    assert reference != fingerprint(_session(0), _session(7, continuation=True, days=2))
    assert reference != fingerprint(
        _session(0, description=SessionDescription(preparation_days=1)),
        _session(7, continuation=True),
    )


def test_fixture(raw_events: list[dict[str, Any]], gcal_settings: GcalSettings) -> None:
    sessions, _ = parse_events(raw_events, gcal_settings)

    engagements, diagnostics = group_sessions(sessions)

    # 193 events - 35 continuations - 2 short roots; no orphan, the longest
    # gap is exactly 31 days.
    assert len(engagements) == 156
    assert diagnostics == []
    assert sum(len(e.sessions) for e in engagements) == 191
    assert Counter(len(e.sessions) for e in engagements) == {
        1: 139,
        2: 11,
        3: 1,
        4: 1,
        5: 1,
        6: 3,
    }
    assert len({e.fingerprint for e in engagements}) == 156
    assert sum(len({s.kind for s in e.sessions}) == 2 for e in engagements) == 1
