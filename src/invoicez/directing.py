from collections.abc import Iterable, Sequence
from logging import getLogger

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from typer import launch

from .compiling.billing import Biller
from .model.merged_event import MergedEvent
from .scheduling.calendar import Calendar
from .scheduling.mapping import Mapper
from .scheduling.merging import Merger


class Director:
    def __init__(
        self,
        calendar: Calendar,
        merger: Merger,
        mapper: Mapper,
        biller: Biller,
    ):
        self._calendar = calendar
        self._merger = merger
        self._mapper = mapper
        self._biller = biller
        self._logger = getLogger(__name__)

    def direct(self) -> None:
        events = self._calendar.list_events()
        merged_events = self._merger.merge_events(events)
        self._delete_invalid_gcal_infos(merged_events)
        unbilled_events = self._filter_unmatched_past_events(merged_events)
        self._bill_events(unbilled_events)

    def _delete_invalid_gcal_infos(self, merged_events: Iterable[MergedEvent]) -> None:
        self._logger.info("Detecting and deleting invalid Google Calendar IDs")
        invalid_invoices = self._mapper.delete_invalid_gcal_infos(merged_events)
        if invalid_invoices:
            self._logger.info(
                "→ Deleted invalid Google Calendar IDs for the following invoices:"
            )
            self._logger.info(
                "\n".join(f"- {invoice.invoice_number}" for invoice in invalid_invoices)
            )
        else:
            self._logger.info("→ Nothing to delete.")

    def _filter_unmatched_past_events(
        self, events: Iterable[MergedEvent]
    ) -> list[MergedEvent]:
        self._logger.info("Finding unlinked past events to bill them.")
        unbilled_events = self._mapper.filter_unmatched_past_events(events)
        if unbilled_events:
            self._logger.info(f"→ Found {len(unbilled_events)} unbilled events.")
        else:
            self._logger.info("→ Nothing to bill.")
        return unbilled_events

    def _bill_events(self, events: Sequence[MergedEvent]) -> None:
        for event in events[:-1]:
            self._bill_event(event)
            input("Press any key when you are ready for the next invoice")
        if events:
            self._bill_event(events[-1])

    def _bill_event(self, event: MergedEvent) -> None:
        if Confirm.ask(f"Do you want to bill the event {event} now?"):
            invoice = self._biller.bill(event)
            invoice.write()
            console = Console()
            console.print("Here is what the [r].yml[/] looks like:")
            console.print(
                Syntax(
                    invoice.path.read_text(encoding="utf8"),
                    "yaml",
                    theme="ansi_dark",
                )
            )
            if Confirm.ask("Do you want to edit it?"):
                print(str(invoice.path))
                launch(str(invoice.path))
                Prompt.ask("Press enter when you are ready to continue")
