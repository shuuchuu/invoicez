from collections.abc import Iterable

from ..config.paths import Paths
from ..config.settings import Settings
from ..exceptions import InvoicezError
from ..model.event import Event
from ..model.merged_event import MergedEvent


class Merger:
    def __init__(self, paths: Paths, settings: Settings) -> None:
        self._paths = paths
        self._title_pattern = Event.compile_pattern(settings.title_pattern)

    def merge_events(self, events: Iterable[Event]) -> list[MergedEvent]:
        current_events: dict[tuple[str, str, str, str | None], MergedEvent] = {}
        multipart_events = []
        for event in events:
            key = (event.company, event.training, event.place, event.extra)
            if event.continuation:
                if key not in current_events:
                    msg = f"Found follow-up event {event} without root event."
                    raise InvoicezError(msg)
                current_events[key].starts.append(event.start)
                current_events[key].durations.append(event.duration)
            else:
                if key in current_events:
                    multipart_events.append(current_events[key])
                current_events[key] = MergedEvent(
                    company=event.company,
                    training=event.training,
                    place=event.place,
                    description=event.description,
                    link=event.link,
                    id=event.id,
                    starts=[event.start],
                    durations=[event.duration],
                )
        multipart_events.extend(current_events.values())
        return sorted(multipart_events, key=lambda e: e.starts[0])
