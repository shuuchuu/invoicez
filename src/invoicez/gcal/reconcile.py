"""Match engagements with the documents of the data repository.

Documents are only read, never modified.
"""

from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from yaml import YAMLError, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.grouping import Engagement
from invoicez.variables import GcalSource

State = Literal[
    # Ends before gcal.start_date and not billed from the calendar.
    "before_cutoff",
    # An unconfirmed booking: never billed until its title loses the marker.
    "tentative",
    # Not over yet.
    "upcoming",
    # Over and not billed: to draft.
    "unbilled",
    # Billed from the calendar, unchanged since.
    "billed",
    # Billed from the calendar, but its sessions or preparation changed since.
    "changed_since_billing",
    # Billed then cancelled by a credit note: re-billed only on request.
    "billed_then_cancelled",
    # Several invoices still active for it: to sort out by hand.
    "billed_twice",
    # Not billed from the calendar, but its description has an invoice number.
    "billed_by_hand",
]


class GcalDocument(BaseModel, extra="forbid", frozen=True):
    document_number: str
    path: Path
    source: GcalSource


class DocumentIndex(BaseModel, extra="forbid", frozen=True):
    # Invoices with a gcal source, by document number.
    documents: dict[str, GcalDocument]
    # Document numbers of the invoices listing each event.
    by_event: dict[str, tuple[str, ...]]
    # Document numbers cancelled by a credit note.
    cancelled: frozenset[str]


class Classification(BaseModel, extra="forbid", frozen=True):
    state: State
    # The documents that justify the state, if any.
    documents: tuple[str, ...] = ()


class OrphanedDocument(BaseModel, extra="forbid", frozen=True):
    document: GcalDocument
    # Its events no longer part of any engagement (deleted, renamed, ...).
    missing_event_ids: tuple[str, ...]


def iter_invoices(git_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    # Invoices and credit notes; quotes are numbered apart and never billed.
    for path in sorted(git_dir.glob("**/*.yml")):
        try:
            content = safe_load(path.read_text(encoding="utf8"))
        except YAMLError as e:
            msg = f"{path}: not valid YAML ({e})"
            raise InvoicezError(msg) from e
        if (
            isinstance(content, dict)
            and "document_number" in content
            and not content.get("quote")
        ):
            yield path, content


def scan_documents(git_dir: Path) -> DocumentIndex:
    """Index the invoices made from the calendar and the credit notes.

    Returns:
        The index.

    Raises:
        InvoicezError: If a document cannot be read, has an invalid source, or
            shares its number with another.
    """
    documents: dict[str, GcalDocument] = {}
    cancelled: set[str] = set()
    paths: dict[str, Path] = {}
    for path, content in iter_invoices(git_dir):
        number = str(content["document_number"])
        if number in paths:
            msg = f"{paths[number]} and {path} share the number {number}"
            raise InvoicezError(msg)
        paths[number] = path
        if "cancels" in content:
            cancelled.add(str(content["cancels"]))
        if "source" not in content:
            continue
        try:
            source = GcalSource.model_validate(content["source"])
        except ValidationError as e:
            msg = f"{path}: invalid source\n{e}"
            raise InvoicezError(msg) from e
        documents[number] = GcalDocument(
            document_number=number, path=path, source=source
        )

    by_event: dict[str, list[str]] = {}
    for number, document in documents.items():
        for event_id in document.source.event_ids:
            by_event.setdefault(event_id, []).append(number)
    return DocumentIndex(
        documents=documents,
        by_event={event: tuple(numbers) for event, numbers in by_event.items()},
        cancelled=frozenset(cancelled),
    )


def classify(
    engagement: Engagement, index: DocumentIndex, *, today: date, start_date: date
) -> Classification:
    """Tell whether an engagement is to bill, billed, or to look at.

    Returns:
        The state, with the documents it is based on.
    """
    numbers = sorted(
        {
            number
            for session in engagement.sessions
            for number in index.by_event.get(session.event_id, ())
        }
    )
    active = tuple(number for number in numbers if number not in index.cancelled)
    if len(active) > 1:
        return Classification(state="billed_twice", documents=active)
    if active:
        [number] = active
        fingerprint = index.documents[number].source.fingerprint
        state = (
            "billed"
            if fingerprint == engagement.fingerprint
            else "changed_since_billing"
        )
        return Classification(state=state, documents=active)
    if numbers:
        return Classification(state="billed_then_cancelled", documents=tuple(numbers))
    if engagement.description.legacy_invoice is not None:
        return Classification(state="billed_by_hand")
    if engagement.end < start_date:
        return Classification(state="before_cutoff")
    if engagement.tentative:
        return Classification(state="tentative")
    if engagement.end > today:
        return Classification(state="upcoming")
    return Classification(state="unbilled")


def orphaned_documents(
    index: DocumentIndex, engagements: Iterable[Engagement]
) -> list[OrphanedDocument]:
    """List active invoices with events no longer in any engagement.

    The engagements must come from the whole calendar, not only recent events.

    Returns:
        The orphaned documents, by document number.
    """
    event_ids = {
        session.event_id
        for engagement in engagements
        for session in engagement.sessions
    }
    orphans = []
    for number, document in sorted(index.documents.items()):
        if number in index.cancelled:
            continue
        missing = tuple(e for e in document.source.event_ids if e not in event_ids)
        if missing:
            orphans.append(
                OrphanedDocument(document=document, missing_event_ids=missing)
            )
    return orphans
