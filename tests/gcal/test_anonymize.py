import json
from typing import Any

from yaml import safe_load

from invoicez.gcal.anonymize import (
    FREE_TEXT,
    HTML_TEXT,
    anonymize,
    anonymized_settings,
    find_leaks,
    select_fixture_events,
    summarize,
)

# The data repo's pattern: verbose, without ^/$ anchors.
_TITLE_PATTERN = r"""
\s*
(?P<continuation> -> \s+ )?
(?P<company> .*? )
\s+ - \s+
(?P<training> .*? )
\s+ - \s+
(?P<place> .*? )
(?: \s+ - \s+ (?P<extra>.*?) )?
\s*
"""


def _all_day(start: str, end: str) -> dict[str, Any]:
    return {"start": {"date": start}, "end": {"date": end}}


def _timed(start: str, end: str) -> dict[str, Any]:
    return {
        "start": {"dateTime": start, "timeZone": "Europe/Paris"},
        "end": {"dateTime": end, "timeZone": "Europe/Paris"},
    }


_EVENTS: list[dict[str, Any]] = [
    {
        "id": "abc123",
        "htmlLink": "https://calendar.google.com/event?eid=abc123",
        "status": "confirmed",
        "summary": "Orsys - BDA - cv - Session Alpha",
        "description": "ref: BDAZZ1-2026\ninvoice: 2021-01-001\npaid: 2021-02-01",
        "location": "Rue de Rivoli, Paris",
        "attendees": [{"email": "client@orsys.fr"}],
        **_all_day("2026-10-05", "2026-10-07"),
    },
    {
        "id": "def456",
        "htmlLink": "https://calendar.google.com/event?eid=def456",
        "status": "confirmed",
        "summary": "-> Orsys - BDA - cv - Session Alpha",
        "description": "",
        **_timed("2026-10-12T09:00:00+02:00", "2026-10-12T12:30:00+02:00"),
    },
    {
        "id": "ghi789",
        "htmlLink": "https://calendar.google.com/event?eid=ghi789",
        "status": "confirmed",
        "summary": "orsys - bda - paris",
        "description": "Call Martine about the Orsys contract",
        **_all_day("2026-11-02", "2026-11-03"),
    },
    {
        "id": "jkl012",
        "htmlLink": "https://calendar.google.com/event?eid=jkl012",
        "status": "confirmed",
        "summary": "Sparks Formation - BDA - nantes",
        "description": "<p>Confidential <b>notes</b></p>",
        **_all_day("2026-11-09", "2026-11-10"),
    },
    {
        "id": "mno345",
        "htmlLink": "https://calendar.google.com/event?eid=mno345",
        "status": "confirmed",
        "summary": "Dentist appointment",
        "description": "Bring the X-rays",
        **_timed("2026-10-20T08:00:00+02:00", "2026-10-20T09:00:00+02:00"),
    },
    {
        "id": "pqr678",
        "htmlLink": "https://calendar.google.com/event?eid=pqr678",
        "status": "confirmed",
        "summary": "Orsys - BDA",
        **_all_day("2026-12-01", "2026-12-02"),
    },
    {
        "id": "stu901",
        "recurringEventId": "abc123",
        "htmlLink": "https://calendar.google.com/event?eid=stu901",
        "status": "cancelled",
        "summary": "Orsys - MLB - cv",
        "description": "ref: refcv1\ncontact: Martine Dupont",
        **_all_day("2026-12-07", "2026-12-08"),
    },
]


def test_titles() -> None:
    events, _ = anonymize(_EVENTS, _TITLE_PATTERN)

    assert [event["summary"] for event in events] == [
        "Company_0 - TRAINING_0 - cv - Extra_0",
        "-> Company_0 - TRAINING_0 - cv - Extra_0",
        # Same company and training, case shape kept; places other than CV
        # pseudonymised.
        "company_0 - training_0 - place_0",
        # Trainings are numbered per company.
        "Company_1 - TRAINING_0 - place_1",
        # Non-matching: shape kept, known company and training recognised.
        "Other_0",
        "Company_0 - TRAINING_0",
        "Company_0 - TRAINING_1 - cv",
    ]


def test_ids_links_and_kept_fields() -> None:
    events, _ = anonymize(_EVENTS, _TITLE_PATTERN)

    assert [event["id"] for event in events] == [f"Id_{i}" for i in range(7)]
    assert events[6]["recurringEventId"] == "Id_0"
    assert events[0]["htmlLink"] == "HTMLLink_0"
    assert events[6]["status"] == "cancelled"
    assert events[1]["start"] == _EVENTS[1]["start"]
    assert events[0]["end"] == {"date": "2026-10-07"}
    assert all("location" not in e and "attendees" not in e for e in events)


def test_descriptions() -> None:
    events, _ = anonymize(_EVENTS, _TITLE_PATTERN)

    first = safe_load(events[0]["description"])
    assert first["ref"] == "REF_0"
    assert first["invoice"] == "2021-01-001"
    assert str(first["paid"]) == "2021-02-01"
    assert events[1]["description"] == ""
    assert events[2]["description"] == FREE_TEXT
    assert events[3]["description"] == HTML_TEXT
    assert "description" not in events[4]
    last = safe_load(events[6]["description"])
    assert last == {"ref": "ref_1", "contact": "Value_0"}


def test_no_real_value_left() -> None:
    events, pseudonyms = anonymize(_EVENTS, _TITLE_PATTERN)
    dumped = json.dumps(events, ensure_ascii=False)

    assert find_leaks(events, pseudonyms.mapping) == []
    for real in (
        "Orsys",
        "Sparks",
        "BDAZZ1",
        "refcv1",
        "Martine",
        "Alpha",
        "Dentist",
        "Nantes",
        "X-rays",
        "Rivoli",
        "abc123",
        "eid=",
        "Confidential",
    ):
        assert real.casefold() not in dumped.casefold()
    # Paris stays in time zones (Europe/Paris), not in titles.
    assert all("paris" not in event["summary"].casefold() for event in events)


def test_find_leaks_reports_real_values() -> None:
    events, pseudonyms = anonymize(_EVENTS, _TITLE_PATTERN)
    events[2]["description"] = "orsys forgot this"

    assert find_leaks(events, pseudonyms.mapping) == ["Orsys", "orsys"]


def test_mapping() -> None:
    _, pseudonyms = anonymize(_EVENTS, _TITLE_PATTERN)

    assert pseudonyms.mapping["Company_0"] == "Orsys"
    assert pseudonyms.mapping["Company_0/TRAINING_0"] == "BDA"
    assert pseudonyms.mapping["Company_1/TRAINING_0"] == "BDA"
    assert pseudonyms.mapping["Company_1"] == "Sparks Formation"
    assert pseudonyms.mapping["Id_0"] == "abc123"


def test_anonymized_settings() -> None:
    _, pseudonyms = anonymize(_EVENTS, _TITLE_PATTERN)
    gcal_section = {
        "start_date": "2026-10-14",
        "title_pattern": _TITLE_PATTERN,
        "templates": {"day_line": "Journée de formation"},
        "rates": {"t2": {"day": 690}},
        "trainings": {"orsys": {"bda": {"name": "Big Data", "rate": "t2"}}},
    }

    section, unknown = anonymized_settings(gcal_section, pseudonyms)

    assert section["rates"] == {"t2": {"day": 690}}
    assert section["title_pattern"] == _TITLE_PATTERN
    assert section["trainings"] == {
        "company_0": {
            "training_0": {"name": "Training 0 of Company_0", "rate": "t2"},
            "training_1": {"name": "Training 1 of Company_0", "rate": "TODO"},
        },
        "company_1": {
            "training_0": {"name": "Training 0 of Company_1", "rate": "TODO"},
        },
    }
    assert unknown == ["Company_0/Training_1", "Company_1/Training_0"]


def test_summarize() -> None:
    events, _ = anonymize(_EVENTS, _TITLE_PATTERN)

    summary = summarize(events, _TITLE_PATTERN)

    assert "events: 7\n" in summary
    assert "status: cancelled: 1, confirmed: 6\n" in summary
    assert "recurring instances: 1\n" in summary
    assert "non-matching titles: 2\n" in summary
    assert "matching titles: 5\n" in summary
    assert "kinds: all-day: 4, timed: 1\n" in summary
    assert "lengths: 1 day(s): 3, 2 day(s): 1, 3.5 h: 1\n" in summary
    assert "continuations: 1\n" in summary
    assert "training token case: lower: 1, upper: 4\n" in summary
    assert "descriptions: HTML: 1, YAML: 2, empty: 1, free text: 1\n" in summary
    assert "YAML description keys: contact: 1, invoice: 1, paid: 1, ref: 2\n" in summary


def test_find_leaks_ignores_pseudonym_shaped_values() -> None:
    events = [{"summary": "Company_0 - Training_0 - cv"}]

    assert find_leaks(events, {"Company_0": "Company_0", "Id_0": "abc123"}) == []


def test_cv_place_is_kept_whatever_its_case() -> None:
    event = {"summary": "Orsys - BDA - CV", **_all_day("2026-10-05", "2026-10-06")}

    events, _ = anonymize([event], _TITLE_PATTERN)

    assert events[0]["summary"] == "Company_0 - TRAINING_0 - CV"


def test_select_fixture_events() -> None:
    events, _ = anonymize(_EVENTS, _TITLE_PATTERN)

    selected = select_fixture_events(events, _TITLE_PATTERN)

    # The one-segment personal event is left out.
    assert [event["id"] for event in selected] == [
        "Id_0",
        "Id_1",
        "Id_2",
        "Id_3",
        "Id_5",
        "Id_6",
    ]


def test_select_fixture_events_leaves_out_recurring_two_segment_events() -> None:
    birthday = {
        "summary": "Birthday - Alice",
        "recurringEventId": "series",
        **_all_day("1952-02-05", "1952-02-06"),
    }
    one_off = {"summary": "Call - Bob", **_all_day("2026-10-05", "2026-10-06")}

    assert select_fixture_events([birthday, one_off], _TITLE_PATTERN) == [one_off]


def test_tentative_marker_is_kept() -> None:
    pattern = _TITLE_PATTERN.replace("\\s*\n", "\\s*\n(?P<tentative> \\? \\s+ )?\n", 1)
    event = {"summary": "? Orsys - BDA - CV", **_all_day("2026-10-05", "2026-10-06")}

    events, _ = anonymize([event], pattern)

    assert events[0]["summary"] == "? Company_0 - TRAINING_0 - CV"
