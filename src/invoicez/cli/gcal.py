from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from rich.console import Console
from rich.table import Table

from invoicez.gcal.diagnostics import Diagnostic
from invoicez.gcal.loading import (
    Analysis,
    EngagementStatus,
    analyse,
    google_api,
    load_events,
    load_gcal,
)
from invoicez.gcal.reconcile import State, scan_documents
from invoicez.paths import Paths

app = App(name="gcal", help="Bill trainings from a Google Calendar.")

# What status shows without --all: what needs action, then what is coming.
_ACTION_STATES: tuple[State, ...] = (
    "billed_twice",
    "changed_since_billing",
    "unbilled",
    "billed_then_cancelled",
    "upcoming",
)
_LEVELS = ("error", "warning", "info")
_LEVEL_STYLES = {"error": "bold red", "warning": "yellow", "info": "dim"}


def _today() -> date:
    return date.today()


@app.command
def calendars() -> None:
    """List your Google calendars, to set gcal.calendar_id in settings.yml."""
    google = google_api()
    entries = google.list_calendars(google.calendar_service(google.credentials()))
    table = Table("Id", "Name", "Primary")
    for entry in entries:
        table.add_row(
            entry["id"], entry.get("summary", ""), "yes" if entry.get("primary") else ""
        )
    Console().print(table)


def _days(status: EngagementStatus) -> float:
    if status.pricing is not None:
        return status.pricing.days
    return sum(
        s.quantity * (0.5 if s.kind == "half_day" else 1)
        for s in status.engagement.sessions
    )


def _amount(status: EngagementStatus) -> str:
    if status.pricing is None:
        return ""
    return f"{sum(line.n * line.pu for line in status.pricing.lines):.2f}"


def _event_ends(raw_events: Iterable[dict[str, Any]]) -> dict[str, date]:
    # Last date of each event, to hide diagnostics about old events.
    ends: dict[str, date] = {}
    for event in raw_events:
        end = event.get("end") or {}
        value = end.get("date") or end.get("dateTime")
        try:
            ends[event["id"]] = date.fromisoformat(str(value)[:10])
        except (KeyError, ValueError):
            continue
    return ends


def _print_engagements(
    console: Console, statuses: list[EngagementStatus], show_all: bool
) -> None:
    shown = [
        s for s in statuses if show_all or s.classification.state in _ACTION_STATES
    ]
    order = {state: i for i, state in enumerate(_ACTION_STATES)}
    shown.sort(
        key=lambda s: (
            order.get(s.classification.state, len(order)),
            s.engagement.start,
        )
    )
    table = Table(
        "State", "Start", "End", "Company", "Training", "Ref", "Days", "Amount",
        "Documents", "Id",
        title="Engagements" if show_all else "Engagements needing action, and upcoming",
    )  # fmt: skip
    for s in shown:
        e = s.engagement
        table.add_row(
            s.classification.state,
            str(e.start),
            str(e.end),
            e.company,
            e.training,
            e.description.ref or "",
            f"{_days(s):g}",
            _amount(s),
            ", ".join(s.classification.documents),
            e.id,
        )
    console.print(table)
    hidden = len(statuses) - len(shown)
    if hidden:
        console.print(
            f"{hidden} other engagements (billed, or before start_date): --all"
        )


def _print_diagnostics(
    console: Console, diagnostics: list[Diagnostic], hidden: int
) -> None:
    if diagnostics:
        console.print("\nDiagnostics:")
    for diagnostic in sorted(diagnostics, key=lambda d: _LEVELS.index(d.level)):
        console.print(
            f"[{_LEVEL_STYLES[diagnostic.level]}]{diagnostic.level}[/]: "
            f"{diagnostic.message}",
            highlight=False,
        )
    if hidden:
        console.print(f"{hidden} diagnostics about events before start_date: --all")


def _shown_diagnostics(
    analysis: Analysis,
    raw_events: list[dict[str, Any]],
    start_date: date,
    show_all: bool,
) -> tuple[list[Diagnostic], int]:
    diagnostics = [
        *analysis.diagnostics,
        *(d for s in analysis.engagements for d in s.diagnostics),
    ]
    if show_all:
        return diagnostics, 0
    ends = _event_ends(raw_events)
    shown = [
        d
        for d in diagnostics
        if d.level != "info"
        and (d.event_id not in ends or ends[d.event_id] >= start_date)
    ]
    return shown, len(diagnostics) - len(shown)


@app.command
def status(
    *,
    show_all: Annotated[bool, Parameter(name="--all", negative="")] = False,
    events: Path | None = None,
) -> int:
    """Show the calendar's engagements and whether they are billed.

    Args:
        show_all: Also show billed engagements, those before gcal.start_date, and
            every diagnostic.
        events: Read the events from this JSON file instead of Google Calendar.

    Returns:
        1 if an error or an engagement billed twice needs fixing, else 0.
    """
    paths = Paths()
    context = load_gcal(paths.settings, paths.variables)
    raw_events = load_events(context.gcal, events_file=events)
    analysis = analyse(
        raw_events, context.gcal, scan_documents(paths.git_dir), today=_today()
    )

    console = Console()
    _print_engagements(console, list(analysis.engagements), show_all)
    diagnostics, hidden = _shown_diagnostics(
        analysis, raw_events, context.gcal.start_date, show_all
    )
    _print_diagnostics(console, diagnostics, hidden)
    for orphan in analysis.orphans:
        console.print(
            f"[yellow]orphaned[/]: {orphan.document.document_number} "
            f"({orphan.document.path.relative_to(paths.git_dir)}): events "
            f"{', '.join(orphan.missing_event_ids)} are no longer in the calendar",
            highlight=False,
        )

    failed = any(d.level == "error" for d in diagnostics) or any(
        s.classification.state == "billed_twice" for s in analysis.engagements
    )
    return 1 if failed else 0
