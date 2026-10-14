"""Link invoices billed before invoicez read the calendar to their engagements.

Invoices are matched by the days they bill. Linking appends a gcal source to
the invoice file; nothing else in it changes.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from yaml import safe_dump, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.grouping import Engagement
from invoicez.gcal.reconcile import iter_invoices

# Dated preparation lines are not training days (e.g. 2021-10-002).
_PREPARATION_RE = re.compile(r"pr[ée]paration", re.IGNORECASE)


class UnlinkedInvoice(BaseModel, extra="forbid", frozen=True):
    document_number: str
    path: Path
    # Days billed by its dated per-day lines, preparation aside.
    days: frozenset[date]


class Link(BaseModel, extra="forbid", frozen=True):
    engagement: Engagement
    invoice: UnlinkedInvoice
    # exact: same days; description: the invoice its description names
    # (invoice: …) among those sharing days; overlap: the only invoice sharing
    # days; pair: by hand.
    how: Literal["exact", "overlap", "description", "pair"]


class Review(BaseModel, extra="forbid", frozen=True):
    engagement: Engagement
    # several: several invoices share its days; none: no invoice does;
    # shared: its only invoice is also the only one of another engagement.
    reason: Literal["several", "none", "shared"]
    candidates: tuple[str, ...]


class LinkPlan(BaseModel, extra="forbid", frozen=True):
    links: tuple[Link, ...]
    reviews: tuple[Review, ...]
    # Invoices with training days that no engagement shares.
    unmatched: tuple[UnlinkedInvoice, ...]


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _training_days(products: Iterable[Mapping[str, Any]]) -> frozenset[date]:
    days: set[date] = set()
    for product in products:
        if (
            product.get("unit") != "DAY"
            or "start" not in product
            or _PREPARATION_RE.search(str(product.get("description", "")))
        ):
            continue
        start = _as_date(product["start"])
        end = _as_date(product.get("end") or start)
        days |= {start + timedelta(days=i) for i in range((end - start).days + 1)}
    return frozenset(days)


def scan_unlinked_invoices(git_dir: Path) -> list[UnlinkedInvoice]:
    """List the active invoices without a source that bill dated days.

    Returns:
        The invoices, by path.
    """
    cancelled: set[str] = set()
    invoices: list[UnlinkedInvoice] = []
    for path, content in iter_invoices(git_dir):
        if "cancels" in content:
            cancelled.add(str(content["cancels"]))
            continue
        if "source" in content:
            continue
        days = _training_days(content.get("products") or [])
        if days:
            invoices.append(
                UnlinkedInvoice(
                    document_number=str(content["document_number"]),
                    path=path,
                    days=days,
                )
            )
    return [i for i in invoices if i.document_number not in cancelled]


def engagement_days(engagement: Engagement) -> frozenset[date]:
    return frozenset(
        session.start + timedelta(days=i)
        for session in engagement.sessions
        for i in range((session.end - session.start).days + 1)
    )


def _paired(
    engagements: Mapping[str, Engagement],
    invoices: Mapping[str, UnlinkedInvoice],
    pairs: Mapping[str, str],
) -> list[Link]:
    errors = [
        f"--pair {engagement_id}={number}: "
        + (
            "no such engagement to link"
            if engagement_id not in engagements
            else "no such invoice to link"
        )
        for engagement_id, number in pairs.items()
        if engagement_id not in engagements or number not in invoices
    ]
    numbers = list(pairs.values())
    errors += [
        f"--pair: {number} given several times"
        for number in sorted(set(numbers))
        if numbers.count(number) > 1
    ]
    if errors:
        raise InvoicezError("\n".join(errors))
    return [
        Link(engagement=engagements[e], invoice=invoices[n], how="pair")
        for e, n in pairs.items()
    ]


def plan_links(
    engagements: Iterable[Engagement],
    invoices: Iterable[UnlinkedInvoice],
    pairs: Mapping[str, str] | None = None,
) -> LinkPlan:
    """Match engagements with the invoices that billed them.

    Args:
        engagements: Engagements without a linked invoice.
        invoices: Invoices without a source, from scan_unlinked_invoices.
        pairs: Engagement id -> invoice number, decided by hand; they take
            precedence and leave the automatic matching. A pair naming an
            unknown engagement or invoice, or an invoice twice, raises
            InvoicezError.

    Returns:
        The links, the engagements to review, and the unmatched invoices.
    """
    engagements_by_id = {e.id: e for e in engagements}
    invoices_by_number = {i.document_number: i for i in invoices}
    links = _paired(engagements_by_id, invoices_by_number, pairs or {})
    paired_engagements = {link.engagement.id for link in links}
    paired_invoices = {link.invoice.document_number for link in links}
    available = [i for n, i in invoices_by_number.items() if n not in paired_invoices]

    candidates: dict[str, list[UnlinkedInvoice]] = {}
    for engagement in engagements_by_id.values():
        if engagement.id in paired_engagements:
            continue
        days = engagement_days(engagement)
        candidates[engagement.id] = [i for i in available if i.days & days]

    # Each engagement's choice: the invoice its description names, if it is a
    # candidate, else its only candidate.
    choices: dict[str, UnlinkedInvoice] = {}
    for engagement_id, found in candidates.items():
        hint = engagements_by_id[engagement_id].description.legacy_invoice
        named = [i for i in found if i.document_number == hint]
        if named or len(found) == 1:
            choices[engagement_id] = (named or found)[0]
    chosen = [invoice.document_number for invoice in choices.values()]

    reviews: list[Review] = []
    for engagement_id, found in candidates.items():
        engagement = engagements_by_id[engagement_id]
        choice = choices.get(engagement_id)
        if choice is not None and chosen.count(choice.document_number) == 1:
            how = (
                "exact"
                if choice.days == engagement_days(engagement)
                else "description"
                if choice.document_number == engagement.description.legacy_invoice
                else "overlap"
            )
            links.append(Link(engagement=engagement, invoice=choice, how=how))
        elif engagement.description.legacy_invoice is None:
            reason = "shared" if choice else "several" if found else "none"
            numbers = tuple(i.document_number for i in found)
            reviews.append(
                Review(engagement=engagement, reason=reason, candidates=numbers)
            )
        # Otherwise accounted for by its hand-written invoice number: one older
        # than the repository, or one invoice for several engagements.

    used = {link.invoice.document_number for link in links} | {
        invoice.document_number for found in candidates.values() for invoice in found
    }
    links.sort(key=lambda link: link.engagement.start)
    reviews.sort(key=lambda review: review.engagement.start)
    return LinkPlan(
        links=tuple(links),
        reviews=tuple(reviews),
        unmatched=tuple(i for i in available if i.document_number not in used),
    )


def source_block(engagement: Engagement, calendar_id: str) -> str:
    # The YAML of the source to append to a linked invoice.
    source = {
        "kind": "gcal",
        "calendar_id": calendar_id,
        "event_ids": [session.event_id for session in engagement.sessions],
        "fingerprint": engagement.fingerprint,
    }
    return safe_dump({"source": source}, allow_unicode=True, sort_keys=False)


def write_link(link: Link, calendar_id: str) -> None:
    """Append the source of a link to its invoice, leaving the rest as is.

    Raises:
        InvoicezError: If the invoice gained a source since it was scanned.
    """
    path = link.invoice.path
    text = path.read_text(encoding="utf8")
    if "source" in (safe_load(text) or {}):
        msg = f"{path} already has a source"
        raise InvoicezError(msg)
    separator = "" if text.endswith("\n") else "\n"
    path.write_text(
        text + separator + source_block(link.engagement, calendar_id),
        encoding="utf8",
    )
