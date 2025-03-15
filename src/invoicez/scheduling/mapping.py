from collections.abc import Iterable, Iterator
from datetime import date
from shutil import copy2
from tempfile import NamedTemporaryFile

from yaml import safe_dump, safe_load

from ..config.paths import Paths
from ..config.settings import Settings
from ..model.invoice import Invoice
from ..model.merged_event import MergedEvent


class Mapper:
    def __init__(self, paths: Paths, settings: Settings):
        self._paths = paths
        self._settings = settings

    @property
    def _gcal_invoices(self) -> Iterator[Invoice]:
        return Invoice.all_from_ymls_dir(self._paths.gcal_ymls_dir)

    @property
    def _extra_invoices(self) -> Iterator[Invoice]:
        return Invoice.all_from_ymls_dir(self._paths.extra_ymls_dir)

    @property
    def _linked_gcal_invoices(self) -> Iterator[Invoice]:
        for invoice in self._gcal_invoices:
            if invoice.gcal_info is not None:
                yield invoice

    @property
    def _unlinked_gcal_invoices(self) -> Iterator[Invoice]:
        for invoice in self._gcal_invoices:
            if invoice.gcal_info is None:
                yield invoice

    def delete_invalid_gcal_infos(
        self, merged_events: Iterable[MergedEvent]
    ) -> list[Invoice]:
        ids = {event.id for event in merged_events}
        invalid_invoices = []
        for invoice in self._linked_gcal_invoices:
            assert invoice.gcal_info is not None
            if invoice.gcal_info.event_id not in ids:
                with invoice.path.open(encoding="utf8") as fh:
                    data = safe_load(fh)
                    if "gcal_info" in data:
                        del data["gcal_info"]
                with NamedTemporaryFile(mode="w", encoding="utf8") as fh:
                    safe_dump(data, fh, allow_unicode=True, default_flow_style=False)
                    copy2(fh.name, invoice.path)
                invalid_invoices.append(invoice)
        return invalid_invoices

    def filter_unmatched_past_events(
        self, merged_events: Iterable[MergedEvent]
    ) -> list[MergedEvent]:
        result = []
        today = date.today()
        linked_event_ids = set()
        for invoice in self._linked_gcal_invoices:
            assert invoice.gcal_info is not None
            linked_event_ids.add(invoice.gcal_info.event_id)
        for event in (
            event for event in merged_events if event.id not in linked_event_ids
        ):
            end = max(
                start + duration.timedelta
                for start, duration in zip(event.starts, event.durations, strict=True)
            )
            end_date = end if isinstance(end, date) else end.date()
            if end_date <= today and end_date >= self._settings.start_date:
                result.append(event)
        return result
