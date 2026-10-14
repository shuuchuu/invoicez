"""Load the settings and the calendar, and analyse them: shared by the gcal commands."""

import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, ValidationError
from yaml import safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.diagnostics import Diagnostic
from invoicez.gcal.grouping import Engagement, group_sessions
from invoicez.gcal.parsing import parse_events
from invoicez.gcal.pricing import Pricing, price
from invoicez.gcal.reconcile import (
    Classification,
    DocumentIndex,
    OrphanedDocument,
    State,
    classify,
    orphaned_documents,
)
from invoicez.gcal.settings import GcalSettings, check_against_variables
from invoicez.settings import Settings

# Engagements that may be drafted, now, once over or once confirmed: priced so
# that missing rates show before billing day. Others need not be in the
# settings any more.
PRICED_STATES: frozenset[State] = frozenset(
    ("unbilled", "upcoming", "tentative", "billed_then_cancelled")
)


class GcalContext(BaseModel, extra="forbid", frozen=True):
    settings: Settings
    gcal: GcalSettings
    # The content of variables.yml.
    global_mapping: dict[str, Any]


class EngagementStatus(BaseModel, extra="forbid", frozen=True):
    engagement: Engagement
    classification: Classification
    # Only for PRICED_STATES, and None when pricing failed.
    pricing: Pricing | None
    diagnostics: tuple[Diagnostic, ...]


class Analysis(BaseModel, extra="forbid", frozen=True):
    engagements: tuple[EngagementStatus, ...]
    # From parsing and grouping; pricing ones are in each engagement.
    diagnostics: tuple[Diagnostic, ...]
    orphans: tuple[OrphanedDocument, ...]


def load_gcal(settings_path: Path, variables_path: Path) -> GcalContext:
    """Read settings.yml and variables.yml for the gcal commands.

    Returns:
        The settings, their gcal section, and the content of variables.yml.

    Raises:
        InvoicezError: If the settings are invalid, have no gcal section, or
            refer to companies missing from variables.yml.
    """
    try:
        settings = Settings.from_path(settings_path)
    except ValidationError as e:
        msg = f"{settings_path} is invalid:\n{e}"
        raise InvoicezError(msg) from e
    if settings.gcal is None:
        msg = f"{settings_path} has no gcal section"
        raise InvoicezError(msg)
    global_mapping = safe_load(variables_path.read_text(encoding="utf8"))
    check_against_variables(settings.gcal, global_mapping)
    return GcalContext(
        settings=settings, gcal=settings.gcal, global_mapping=global_mapping
    )


def google_api() -> ModuleType:
    """Import the Google adapter, which needs the gcal extra.

    Returns:
        The invoicez.gcal.google module.

    Raises:
        InvoicezError: If the gcal extra is not installed.
    """
    try:
        from invoicez.gcal import google
    except ImportError as e:
        msg = "Google Calendar access needs the gcal extra: pip install invoicez[gcal]"
        raise InvoicezError(msg) from e
    return google


def load_events(
    gcal: GcalSettings, *, events_file: Path | None = None
) -> list[dict[str, Any]]:
    """Return the raw events of the whole calendar.

    History is always fetched: linking, orphan detection and reports need it.

    Args:
        gcal: The gcal settings, for the calendar id.
        events_file: Read the events from this JSON file instead of Google
            Calendar, e.g. the test fixture.

    Returns:
        The raw API events.

    Raises:
        InvoicezError: If the file is not a JSON list, the calendar id is not
            set, or the gcal extra is not installed.
    """
    if events_file is not None:
        events = json.loads(events_file.read_text(encoding="utf8"))
        if not isinstance(events, list):
            msg = f"{events_file} must hold a JSON list of events"
            raise InvoicezError(msg)
        return events
    if gcal.calendar_id is None:
        msg = (
            "gcal.calendar_id is not set in settings.yml: run "
            "'invoicez gcal calendars' to find it"
        )
        raise InvoicezError(msg)
    google = google_api()
    service = google.calendar_service(google.credentials())
    return google.fetch_events(service, gcal.calendar_id)


def _status(
    engagement: Engagement, gcal: GcalSettings, index: DocumentIndex, today: date
) -> EngagementStatus:
    classification = classify(
        engagement, index, today=today, start_date=gcal.start_date
    )
    pricing, diagnostics = (
        price(engagement, gcal) if classification.state in PRICED_STATES else (None, [])
    )
    if classification.state == "tentative" and engagement.end <= today:
        diagnostics.append(
            Diagnostic(
                level="warning",
                event_id=engagement.id,
                message=(
                    f"{engagement.root.title!r}: unconfirmed booking over since "
                    f"{engagement.end}: remove its '?' to bill it, or delete it"
                ),
            )
        )
    return EngagementStatus(
        engagement=engagement,
        classification=classification,
        pricing=pricing,
        diagnostics=tuple(diagnostics),
    )


def analyse(
    raw_events: Sequence[Mapping[str, Any]],
    gcal: GcalSettings,
    index: DocumentIndex,
    *,
    today: date,
) -> Analysis:
    """Parse, group, classify and price the calendar against the documents.

    Returns:
        Every engagement with its state, pricing and diagnostics, the other
        diagnostics, and the orphaned documents.
    """
    sessions, parsing_diagnostics = parse_events(raw_events, gcal)
    engagements, grouping_diagnostics = group_sessions(sessions)
    return Analysis(
        engagements=tuple(_status(e, gcal, index, today) for e in engagements),
        diagnostics=(*parsing_diagnostics, *grouping_diagnostics),
        orphans=tuple(orphaned_documents(index, engagements)),
    )
