from abc import ABC, abstractmethod
from functools import cached_property
from logging import getLogger
from pickle import dump as pickle_dump
from pickle import load as pickle_load
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from rich.console import Console
from rich.prompt import IntPrompt
from rich.rule import Rule

from ..config.paths import Paths
from ..config.settings import Settings
from ..model.event import Event


class Calendar(ABC):
    @abstractmethod
    def list_events(self) -> list[Event]:
        raise NotImplementedError

    @abstractmethod
    def list_raw_events(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def edit_event_description(self, event_id: str, new_description: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def select_calendar(self) -> None:
        raise NotImplementedError


class GoogleCalendar(Calendar):
    def __init__(self, paths: Paths, settings: Settings, console: Console):
        self._paths = paths
        self._console = console
        self._logger = getLogger(__name__)
        self._title_pattern = Event.compile_pattern(settings.title_pattern)

    def list_events(self) -> list[Event]:
        events = []
        for raw_event in self.list_raw_events():
            event = Event.from_gcal_event(raw_event, self._title_pattern)
            if event is not None:
                events.append(event)

        return sorted(events, key=lambda e: e.start)

    def list_raw_events(self) -> list[dict[str, Any]]:
        events = []
        next_sync_token = None
        page_token = None
        while next_sync_token is None:
            result = (
                self._service.events()
                .list(calendarId=self._selected_calendar, pageToken=page_token)
                .execute()
            )
            events.extend(result.get("items", []))
            page_token = result.get("nextPageToken", None)
            next_sync_token = result.get("nextSyncToken", None)
        return events

    def edit_event_description(self, event_id: str, new_description: str) -> None:
        self._service.events().patch(
            calendarId=self._selected_calendar,
            eventId=event_id,
            body={"description": new_description},
        ).execute()

    def select_calendar(self) -> None:
        calendars = []
        next_sync_token = None
        page_token = None
        while next_sync_token is None:
            result = self._service.calendarList().list(pageToken=page_token).execute()
            calendars.extend(result.get("items", []))
            page_token = result.get("nextPageToken", None)
            next_sync_token = result.get("nextSyncToken", None)

        calendars_markdown_list = "\n".join(
            f"[bold yellow]{i}[/bold yellow]. {calendar['summary']}"
            for i, calendar in enumerate(calendars, start=1)
        )
        self._console.print(Rule(":date: Available calendars", align="left"))
        self._console.print()
        self._console.print(calendars_markdown_list)
        self._console.print()
        self._console.print(Rule(":pushpin: Selection", align="left"))
        self._console.print()
        calendar_index = (
            IntPrompt.ask(
                "Please enter the number that corresponds to the calendar to use",
                choices=list(map(str, range(1, len(calendars) + 1))),
                show_choices=False,
            )
            - 1
        )
        selected_calendar = calendars[calendar_index]
        self._selected_calendar = selected_calendar["id"]
        self._console.print()
        self._console.print(
            f":ok_hand: Calendar [bold]{calendars[calendar_index]['summary']}[/bold] "
            "is selected."
        )

    @cached_property
    def _service(self) -> Any:
        if self._paths.gcalendar_credentials.is_file():
            with self._paths.gcalendar_credentials.open("rb") as fh:
                creds = pickle_load(fh)
        else:
            creds = None

        if not creds or not creds.valid:
            ok = False
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    ok = True
                except RefreshError:
                    self._logger.warn("Could not refresh auth token")
            if not ok:
                scopes = [
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
                ]
                flow = InstalledAppFlow.from_client_secrets_file(
                    self._paths.gcalendar_secrets, scopes
                )
                creds = flow.run_local_server(port=0)
            with self._paths.gcalendar_credentials.open("wb") as fh:
                pickle_dump(creds, fh)

        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    @property
    def _selected_calendar(self) -> str:
        if not self._paths.gcalendar_selected_calendar.exists():
            self._logger.info("No selected calendar. Starting the selection procedure.")
            self.select_calendar()
        return self._paths.gcalendar_selected_calendar.read_text(encoding="utf8")

    @_selected_calendar.setter
    def _selected_calendar(self, value: str) -> None:
        self._paths.gcalendar_selected_calendar.parent.mkdir(
            parents=True, exist_ok=True
        )
        self._paths.gcalendar_selected_calendar.write_text(value, encoding="utf8")
