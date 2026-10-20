"""Forecast the invoices of the calendar's engagements not billed yet.

Each engagement is drafted as gcal bill would, dated the day it would be:
once over, and not before today.
"""

from datetime import date
from pathlib import Path

from invoicez.exceptions import InvoicezError
from invoicez.gcal.drafting import draft
from invoicez.gcal.loading import Analysis, GcalContext
from invoicez.gcal.reconcile import State
from invoicez.ledger.entries import Entry, Forecast, document_entry
from invoicez.ledger.settings import LedgerSettings

_FORECASTS: dict[State, Forecast] = {
    "unbilled": "to_bill",
    "upcoming": "sure",
    "tentative": "tentative",
}


def forecast_entries(
    analysis: Analysis,
    context: GcalContext,
    git_dir: Path,
    settings: LedgerSettings,
    *,
    today: date,
) -> tuple[list[Entry], list[tuple[date, str]]]:
    """Draft the engagements to bill, upcoming or tentative as entries.

    Args:
        analysis: The calendar, analysed against the documents.
        context: The gcal settings and the content of variables.yml.
        git_dir: The data repository.
        settings: The ledger settings.
        today: The earliest date of a forecast invoice.

    Returns:
        The entries, by date then engagement id, and warnings about the
        engagements left out, with the date they are about.
    """
    entries: list[Entry] = []
    warnings: list[tuple[date, str]] = []
    for status in analysis.engagements:
        forecast = _FORECASTS.get(status.classification.state)
        if forecast is None:
            continue
        engagement = status.engagement
        day = max(engagement.end, today)
        if status.pricing is None:
            errors = "; ".join(
                d.message for d in status.diagnostics if d.level == "error"
            )
            warnings.append((day, f"not in the forecast: {errors}"))
            continue
        try:
            document = draft(
                engagement,
                status.pricing,
                document_number=engagement.id,
                today=day,
                reference=engagement.description.ref,
                trainees=engagement.description.trainees,
                settings=context.gcal,
                global_mapping=context.global_mapping,
                calendar_id=context.gcal.calendar_id or "",
            )
        except InvoicezError as e:
            warnings.append(
                (day, f"{engagement.root.title!r} not in the forecast: {e}")
            )
            continue
        entry = document_entry(
            document, context.global_mapping, git_dir, settings, None, None
        )
        entries.append(
            entry.model_copy(update={"source": "calendar", "forecast": forecast})
        )
    entries.sort(key=lambda entry: (entry.date, entry.number))
    return entries, warnings
