from datetime import datetime, timedelta
from pathlib import Path

from pytest import fixture, raises

from invoicez.config.paths import Paths
from invoicez.config.settings import Settings
from invoicez.exceptions import InvoicezError
from invoicez.model.duration import Duration, DurationUnit
from invoicez.model.event import Event
from invoicez.scheduling.merging import Merger

from .conftest import FakeCalendar


def make_event(
    continuation: bool = False,
    company: int = 0,
    training: int = 0,
    place: int = 0,
    start_offset: int = 0,
    extra: int = 0,
    duration: int = 1,
) -> Event:
    return Event(
        continuation=continuation,
        company=f"company_{company}",
        training=f"training_{training}",
        place=f"place_{place}",
        extra=f"extra_{extra}",
        link=f"link_{start_offset}",
        id=f"id_{start_offset}",
        start=datetime.now() + timedelta(days=start_offset),
        duration=Duration(n=1, unit=DurationUnit.day),
    )


@fixture
def merger() -> Merger:
    paths = Paths(Path.cwd())
    settings = Settings.load(paths)

    return Merger(paths, settings)


def test_non_existing_root_events(merger: Merger) -> None:
    events = [make_event(continuation=True)]

    with raises(InvoicezError):
        merger.merge_events(events)


def test_root_event_after_follow_up_event(merger: Merger) -> None:
    events = [make_event(continuation=True), make_event(start_offset=1)]

    with raises(InvoicezError):
        merger.merge_events(events)


def test_one_follow_up_event(merger: Merger) -> None:
    root_event = make_event()
    events = [root_event, make_event(continuation=True, start_offset=1)]

    merged_events = merger.merge_events(events)

    assert len(merged_events) == 1

    merged_event = merged_events[0]
    assert merged_event.company == root_event.company
    assert merged_event.training == root_event.training
    assert merged_event.place == root_event.place
    assert merged_event.id == root_event.id
    assert merged_event.link == root_event.link


def test_several_follow_up_events(merger: Merger) -> None:
    root_event = make_event()
    events = [root_event] + [
        make_event(continuation=True, start_offset=i) for i in range(5)
    ]

    merged_events = merger.merge_events(events)

    assert len(merged_events) == 1

    merged_event = merged_events[0]
    assert merged_event.company == root_event.company
    assert merged_event.training == root_event.training
    assert merged_event.place == root_event.place
    assert merged_event.id == root_event.id
    assert merged_event.link == root_event.link


def test_from_sync(merger: Merger, calendar: FakeCalendar) -> None:
    events = calendar.list_events()

    merged_events = merger.merge_events(events)

    assert 0 < len(merged_events) < len(events)
