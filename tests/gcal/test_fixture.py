from typing import Any

from invoicez.gcal.anonymize import compile_title_pattern, select_fixture_events
from invoicez.gcal.settings import GcalSettings


def test_fixture_is_already_selected(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings
) -> None:
    assert select_fixture_events(raw_events, gcal_settings.title_pattern) == raw_events


def test_fixture_counts(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings
) -> None:
    pattern = compile_title_pattern(gcal_settings.title_pattern)
    matching = [e for e in raw_events if pattern.fullmatch(e["summary"])]

    assert len(raw_events) == 200
    assert len(matching) == 193
    assert sum(1 for e in matching if "date" in e["start"]) == 159
