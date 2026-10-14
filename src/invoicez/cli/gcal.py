from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from cyclopts import App, Parameter
from rich.console import Console
from rich.table import Table
from yaml import safe_dump

from invoicez.exceptions import InvoicezError
from invoicez.gcal.diagnostics import Diagnostic
from invoicez.gcal.drafting import draft, draft_path
from invoicez.gcal.linking import (
    LinkPlan,
    plan_links,
    scan_unlinked_invoices,
    write_link,
)
from invoicez.gcal.loading import (
    Analysis,
    EngagementStatus,
    GcalContext,
    analyse,
    google_api,
    load_events,
    load_gcal,
)
from invoicez.gcal.reconcile import State, scan_documents
from invoicez.numbering import get_next_document_number
from invoicez.paths import Paths
from invoicez.runner import run as runner_run
from invoicez.sending import send_document

app = App(name="gcal", help="Bill trainings from a Google Calendar.")

# What status shows without --all: what needs action, then what is coming.
_ACTION_STATES: tuple[State, ...] = (
    "billed_twice",
    "changed_since_billing",
    "unbilled",
    "billed_then_cancelled",
    "upcoming",
    "tentative",
)
_LEVELS = ("error", "warning", "info")
_LEVEL_STYLES = {"error": "bold red", "warning": "yellow", "info": "dim"}


def _today() -> date:
    return date.today()


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return "q"


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
        console.print(
            f"{hidden} diagnostics hidden (infos, or events before start_date): --all"
        )


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


def _refusal(status: EngagementStatus, rebill: bool) -> str | None:
    # Why an engagement asked for by id cannot be billed, if it cannot.
    e = status.engagement
    state = status.classification.state
    documents = ", ".join(status.classification.documents)
    if state == "unbilled" or (state == "billed_then_cancelled" and rebill):
        return None
    if state == "billed_then_cancelled":
        return f"{e.id}: billed then cancelled ({documents}): add --rebill"
    if state == "changed_since_billing":
        return (
            f"{e.id}: changed since it was billed ({documents}): cancel it with "
            f"'invoicez cancel <file>', then 'invoicez gcal bill {e.id} --rebill'"
        )
    return f"{e.id}: {state}, not billable"


def _select(
    analysis: Analysis, ids: tuple[str, ...], rebill: bool
) -> list[EngagementStatus]:
    if not ids:
        return [s for s in analysis.engagements if s.classification.state == "unbilled"]
    by_id = {s.engagement.id: s for s in analysis.engagements}
    refusals = [
        f"{engagement_id}: no such engagement"
        if engagement_id not in by_id
        else _refusal(by_id[engagement_id], rebill)
        for engagement_id in ids
    ]
    if errors := [r for r in refusals if r is not None]:
        raise InvoicezError("\n".join(errors))
    return [by_id[engagement_id] for engagement_id in ids]


def _preview(
    console: Console, status: EngagementStatus, document: dict[str, Any], path: Path
) -> None:
    e = status.engagement
    console.print(
        f"\n[bold]{e.root.title}[/] ({e.id}), {e.start} → {e.end}", highlight=False
    )
    table = Table("Start", "End", "Description", "n", "Unit", "PU", "Amount")
    total = 0.0
    for product in document["products"]:
        amount = product["n"] * product["pu"]
        total += amount
        table.add_row(
            str(product.get("start", "")),
            str(product.get("end", "")),
            product["description"],
            f"{product['n']:g}",
            product["unit"],
            f"{product['pu']:.2f}",
            f"{amount:.2f}",
        )
    console.print(table)
    console.print(
        f"Total: {total:.2f}\nDescription: {document['description']}\n"
        f"Reference: {document.get('reference', '')}\n"
        f"Trainees: {document.get('trainees', 'unknown')}\nFile: {path}",
        highlight=False,
    )


def _ask_trainees(
    console: Console, status: EngagementStatus
) -> int | Literal["quit"] | None:
    # The number of trainees, None if unknown, or "quit".
    e = status.engagement
    while True:
        answer = _ask(
            f"Trainees for {e.root.title!r} ({e.start} → {e.end}), "
            "empty if unknown, [q]uit: "
        ).strip()
        if not answer:
            return None
        if answer.casefold().startswith("q"):
            return "quit"
        if answer.isdigit():
            return int(answer)
        console.print(f"[bold red]error[/]: {answer!r} is not a number of trainees")


def _bill_one(
    console: Console,
    status: EngagementStatus,
    context: GcalContext,
    paths: Paths,
    today: date,
    calendar_id: str,
) -> tuple[Literal["written", "skipped", "failed", "quit"], Path | None]:
    e = status.engagement
    if status.pricing is None:
        for diagnostic in status.diagnostics:
            console.print(f"[bold red]error[/]: {diagnostic.message}", highlight=False)
        return "failed", None
    reference = (
        e.description.ref
        or _ask(
            f"\nReference for {e.root.title!r} ({e.start} → {e.end}), empty to skip: "
        ).strip()
    )
    if not reference:
        return "skipped", None
    trainees = e.description.trainees
    if trainees is None:
        answer = _ask_trainees(console, status)
        if answer == "quit":
            return "quit", None
        trainees = answer
    try:
        document = draft(
            e,
            status.pricing,
            document_number=get_next_document_number(paths.git_dir, today=today),
            today=today,
            reference=reference,
            trainees=trainees,
            settings=context.gcal,
            global_mapping=context.global_mapping,
            calendar_id=calendar_id,
        )
    except InvoicezError as error:
        console.print(f"[bold red]error[/]: {error}", highlight=False)
        return "failed", None
    path = paths.git_dir / draft_path(
        document,
        name=reference,
        settings=context.settings,
        global_mapping=context.global_mapping,
        git_dir=paths.git_dir,
    )
    _preview(console, status, document, path.relative_to(paths.git_dir))
    if path.exists():
        console.print(f"[bold red]error[/]: {path} already exists", highlight=False)
        return "failed", None
    answer = _ask("Write it? [y]es, [n]o, [q]uit: ").strip().casefold()
    if answer.startswith("q"):
        return "quit", None
    if not answer.startswith("y"):
        return "skipped", None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf8"
    )
    console.print(f"Wrote {path.relative_to(paths.git_dir)}", highlight=False)
    return "written", path


@app.command
def bill(
    *ids: str,
    rebill: bool = False,
    build: bool = False,
    send: bool = False,
    events: Path | None = None,
) -> int:
    """Draft invoices for engagements that are over, one at a time.

    Each draft is previewed and written only once confirmed; its number is the
    next free one at that moment.

    Args:
        ids: Engagements to bill (the Id column of status). Defaults to every
            unbilled engagement.
        rebill: Allow billing again engagements whose invoice was cancelled.
        build: Build the PDF and XML of each written invoice.
        send: Send each written invoice via SuperPDP, once confirmed (see
            invoicez send).
        events: Read the events from this JSON file instead of Google Calendar.

    Returns:
        1 if an engagement could not be drafted or an invoice sent, else 0.

    Raises:
        InvoicezError: If gcal.calendar_id is not set, or an engagement asked
            for cannot be billed.
    """
    paths = Paths()
    context = load_gcal(paths.settings, paths.variables)
    calendar_id = context.gcal.calendar_id
    if calendar_id is None:
        msg = "gcal.calendar_id is not set in settings.yml: see invoicez gcal calendars"
        raise InvoicezError(msg)
    today = _today()
    analysis = analyse(
        load_events(context.gcal, events_file=events),
        context.gcal,
        scan_documents(paths.git_dir),
        today=today,
    )
    selected = _select(analysis, ids, rebill)
    console = Console()
    if not selected:
        console.print("Nothing to bill.")
        return 0
    outcomes: list[str] = []
    sent, send_failed = 0, False
    for status in selected:
        outcome, path = _bill_one(console, status, context, paths, today, calendar_id)
        outcomes.append(outcome)
        if outcome == "quit":
            break
        if path is not None and send:
            try:
                if send_document(console, paths, path) == "sent":
                    sent += 1
            except InvoicezError as error:
                console.print(f"[bold red]error[/]: {error}", highlight=False)
                send_failed = True
        elif path is not None and build:
            runner_run(
                path,
                "main",
                paths.variables,
                paths.jinja2_dir,
                paths.git_dir,
                paths.assets_dir,
                paths.build_dir,
                paths.sendbox_dir,
            )
    console.print(
        f"\n{outcomes.count('written')} written, {outcomes.count('skipped')} "
        f"skipped, {outcomes.count('failed')} failed"
        + (f", {sent} sent." if send else ".")
    )
    return 1 if "failed" in outcomes or send_failed else 0


def _parse_pairs(pairs: tuple[str, ...]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for pair in pairs:
        engagement_id, separator, number = pair.partition("=")
        if not separator or not engagement_id or not number:
            msg = f"--pair {pair}: expected ENGAGEMENT_ID=DOCUMENT_NUMBER"
            raise InvoicezError(msg)
        parsed[engagement_id] = number
    return parsed


def _print_plan(console: Console, plan: LinkPlan, git_dir: Path) -> None:
    links = Table(
        "Start", "End", "Company", "Training", "Ref", "Invoice", "File", "How",
        title="Links",
    )  # fmt: skip
    for link in plan.links:
        e = link.engagement
        links.add_row(
            str(e.start),
            str(e.end),
            e.company,
            e.training,
            e.description.ref or "",
            link.invoice.document_number,
            str(link.invoice.path.relative_to(git_dir)),
            link.how,
        )
    console.print(links)
    if plan.reviews:
        reviews = Table(
            "Start", "End", "Company", "Training", "Ref", "Reason", "Candidates", "Id",
            title="To review (--pair ID=NUMBER, or fix the calendar or invoice)",
        )  # fmt: skip
        for review in plan.reviews:
            e = review.engagement
            reviews.add_row(
                str(e.start),
                str(e.end),
                e.company,
                e.training,
                e.description.ref or "",
                review.reason,
                ", ".join(review.candidates),
                e.id,
            )
        console.print(reviews)
    if plan.unmatched:
        unmatched = Table(
            "Invoice", "File", "First day", "Last day",
            title="Invoices matching no engagement",
        )  # fmt: skip
        for invoice in plan.unmatched:
            unmatched.add_row(
                invoice.document_number,
                str(invoice.path.relative_to(git_dir)),
                str(min(invoice.days)),
                str(max(invoice.days)),
            )
        console.print(unmatched)


@app.command
def link(
    *,
    write: bool = False,
    pair: tuple[str, ...] = (),
    events: Path | None = None,
) -> None:
    """Link invoices billed before invoicez read the calendar to their events.

    Dry run by default: lists the links it would make, by the days invoices
    bill, and what needs a decision. --write appends a gcal source to each
    linked invoice, leaving the rest of the file as is; gcal.start_date can
    then go back to the first invoice.

    Args:
        write: Append the sources to the linked invoices.
        pair: Link an engagement to an invoice by hand, as
            ENGAGEMENT_ID=DOCUMENT_NUMBER (the Id column); repeatable.
        events: Read the events from this JSON file instead of Google Calendar.

    Raises:
        InvoicezError: If gcal.calendar_id is not set, or a pair is invalid.
    """
    paths = Paths()
    context = load_gcal(paths.settings, paths.variables)
    calendar_id = context.gcal.calendar_id
    if calendar_id is None:
        msg = "gcal.calendar_id is not set in settings.yml: see invoicez gcal calendars"
        raise InvoicezError(msg)
    pairs = _parse_pairs(pair)
    today = _today()
    analysis = analyse(
        load_events(context.gcal, events_file=events),
        context.gcal,
        scan_documents(paths.git_dir),
        today=today,
    )
    engagements = [
        s.engagement
        for s in analysis.engagements
        if not s.classification.documents and s.engagement.end <= today
    ]
    plan = plan_links(engagements, scan_unlinked_invoices(paths.git_dir), pairs)

    console = Console()
    _print_plan(console, plan, paths.git_dir)
    if write:
        for found in plan.links:
            write_link(found, calendar_id)
        console.print(f"\nLinked {len(plan.links)} invoices.")
    else:
        console.print(
            f"\n{len(plan.links)} links, {len(plan.reviews)} to review, "
            f"{len(plan.unmatched)} invoices unmatched. Add --write to link."
        )
