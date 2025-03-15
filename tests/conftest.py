from json import load
from pathlib import Path
from shutil import copytree
from typing import Any

from git import Repo
from pytest import fixture

from invoicez.config.paths import Paths
from invoicez.config.settings import Settings
from invoicez.model.event import Event
from invoicez.scheduling.calendar import Calendar


@fixture
def paths() -> Paths:
    return Paths(Path.cwd())


@fixture
def settings(paths: Paths) -> Settings:
    return Settings.load(paths)


@fixture
def assets_dir() -> Path:
    return Path(__file__).parent / "assets"


@fixture(autouse=True)
def working_dir(assets_dir: Path, tmp_path: Path, monkeypatch: Any) -> None:
    source_dir = assets_dir / "test_cli"
    working_dir = tmp_path / "data"
    copytree(source_dir, working_dir)
    Repo.init(str(working_dir))
    monkeypatch.chdir(working_dir)


class FakeCalendar(Calendar):
    def __init__(self, paths: Paths, settings: Settings, events_json: Path):
        self.events_json = events_json
        self.title_pattern = Event.compile_pattern(settings.title_pattern)

    def list_events(self) -> list[Event]:
        with self.events_json.open(encoding="utf8") as fh:
            events_json = load(fh)
        events = []
        for gcal_event in events_json:
            event = Event.from_gcal_event(gcal_event, self.title_pattern)
            if event is not None:
                events.append(event)
        return sorted(events, key=lambda e: e.start)

    def list_raw_events(self) -> list[dict[str, Any]]:
        return []

    def edit_event_description(self, event_id: str, new_description: str) -> None:
        pass

    def select_calendar(self) -> None:
        pass


@fixture
def calendar(assets_dir: Path, paths: Paths, settings: Settings) -> FakeCalendar:
    return FakeCalendar(paths, settings, assets_dir / "events.json")
