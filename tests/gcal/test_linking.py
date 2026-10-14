from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pytest import raises
from yaml import safe_dump, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.grouping import Engagement, group_sessions
from invoicez.gcal.linking import (
    Link,
    UnlinkedInvoice,
    plan_links,
    scan_unlinked_invoices,
    write_link,
)
from invoicez.gcal.parsing import Session, SessionDescription
from invoicez.gcal.reconcile import classify, scan_documents

_MONDAY = date(2025, 10, 6)


def _engagement(event_id: str, *days: tuple[int, int]) -> Engagement:
    # Sessions as (first day, number of days) from _MONDAY.
    sessions = [
        Session(
            event_id=f"{event_id}_{i}" if i else event_id,
            html_link="link",
            title="Orsys - BDA - cv",
            continuation=i > 0,
            company="Orsys",
            training="BDA",
            place="cv",
            extra=event_id,
            start=_MONDAY + timedelta(days=first),
            end=_MONDAY + timedelta(days=first + length - 1),
            kind="day",
            quantity=length,
            description=SessionDescription(),
        )
        for i, (first, length) in enumerate(days)
    ]
    [engagement], _ = group_sessions(sessions)
    return engagement


def _invoice(number: str, *days: int) -> UnlinkedInvoice:
    return UnlinkedInvoice(
        document_number=number,
        path=Path(f"{number}.yml"),
        days=frozenset(_MONDAY + timedelta(days=d) for d in days),
    )


def _write(git_dir: Path, name: str, content: dict[str, Any]) -> Path:
    path = git_dir / f"{name}.yml"
    path.write_text(safe_dump(content, allow_unicode=True), encoding="utf8")
    return path


def _line(start: str, end: str | None = None, **extra: Any) -> dict[str, Any]:
    line = {"start": start, "n": 1, "pu": 690, "unit": "DAY", "description": "Jour"}
    if end:
        line["end"] = end
    return {**line, **extra}


def _doc(number: str, *products: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"document_number": number, "products": list(products), **extra}


def test_scan_unlinked_invoices(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "plain",
        {
            "document_number": "2025-10-001",
            "products": [
                _line("2025-10-06", "2025-10-07"),
                # Preparation and lump sums are not training days.
                _line("2025-10-01", description="Journée de préparation"),
                {"n": 1, "pu": 300, "unit": "LS", "description": "Frais"},
            ],
        },
    )
    lump = {"n": 1, "pu": 1, "unit": "LS", "description": "QCM"}
    for name, content in {
        "linked": _doc("2025-10-002", _line("2025-10-08"), source={"kind": "gcal"}),
        "cancelled": _doc("2025-10-003", _line("2025-10-09")),
        "credit": {"document_number": "2025-10-004", "cancels": "2025-10-003"},
        "lump": _doc("2025-10-005", lump),
        # Quotes are numbered apart.
        "quote": _doc("2025-10-001", _line("2025-10-10"), quote=True),
    }.items():
        _write(tmp_path, name, content)

    [invoice] = scan_unlinked_invoices(tmp_path)

    assert invoice.document_number == "2025-10-001"
    assert invoice.days == {date(2025, 10, 6), date(2025, 10, 7)}


def test_exact_and_overlap() -> None:
    exact = _engagement("exact", (0, 2), (7, 1))
    overlap = _engagement("overlap", (14, 3))

    plan = plan_links(
        [exact, overlap], [_invoice("A", 0, 1, 7), _invoice("B", 13, 14, 15)]
    )

    assert [
        (link.engagement.id, link.invoice.document_number, link.how)
        for link in plan.links
    ] == [
        ("exact", "A", "exact"),
        ("overlap", "B", "overlap"),
    ]
    assert plan.reviews == plan.unmatched == ()


def test_reviews_and_unmatched() -> None:
    several = _engagement("several", (0, 3))
    none = _engagement("none", (21, 1))
    # Morning and afternoon groups billed together.
    morning = _engagement("morning", (28, 1))
    afternoon = _engagement("afternoon", (28, 1))

    plan = plan_links(
        [several, none, morning, afternoon],
        [_invoice("A", 0), _invoice("B", 2), _invoice("C", 28), _invoice("D", 35)],
    )

    assert plan.links == ()
    assert [(r.engagement.id, r.reason, r.candidates) for r in plan.reviews] == [
        ("several", "several", ("A", "B")),
        ("none", "none", ()),
        ("morning", "shared", ("C",)),
        ("afternoon", "shared", ("C",)),
    ]
    assert [i.document_number for i in plan.unmatched] == ["D"]


def test_pairs() -> None:
    typo = _engagement("typo", (0, 2))
    other = _engagement("other", (100, 2))
    # The invoice of "other" has its days wrong, and overlaps "typo".
    invoices = [_invoice("A", 0, 1), _invoice("B", 1)]

    plan = plan_links([typo, other], invoices, {"other": "B"})

    assert [
        (link.engagement.id, link.invoice.document_number, link.how)
        for link in plan.links
    ] == [
        ("typo", "A", "exact"),
        ("other", "B", "pair"),
    ]
    assert plan.reviews == ()


def test_invalid_pairs() -> None:
    engagement = _engagement("e", (0, 1))
    other = _engagement("f", (7, 1))
    invoices = [_invoice("A", 0)]

    with raises(InvoicezError) as error:
        plan_links([engagement, other], invoices, {"x": "A", "e": "Z"})

    assert str(error.value).splitlines() == [
        "--pair x=A: no such engagement to link",
        "--pair e=Z: no such invoice to link",
    ]
    with raises(InvoicezError, match="A given several times"):
        plan_links([engagement, other], invoices, {"e": "A", "f": "A"})


def test_write_link(tmp_path: Path) -> None:
    engagement = _engagement("event", (0, 2))
    text = (
        "# Hand-written.\n"
        "document_number: 2025-10-001\n"
        "products:\n"
        "- {start: 2025-10-06, end: 2025-10-07, n: 2, pu: 690, unit: DAY, "
        "description: Jour}"
    )
    path = tmp_path / "invoice.yml"
    path.write_text(text, encoding="utf8")
    [invoice] = scan_unlinked_invoices(tmp_path)

    write_link(Link(engagement=engagement, invoice=invoice, how="exact"), "cal")

    written = path.read_text(encoding="utf8")
    assert written.startswith(text + "\n")
    assert safe_load(written)["source"] == {
        "kind": "gcal",
        "calendar_id": "cal",
        "event_ids": ["event"],
        "fingerprint": engagement.fingerprint,
    }
    index = scan_documents(tmp_path)
    classification = classify(
        engagement, index, today=date(2026, 10, 14), start_date=date(2020, 1, 1)
    )
    assert classification.state == "billed"
    with raises(InvoicezError, match="already has a source"):
        write_link(Link(engagement=engagement, invoice=invoice, how="exact"), "cal")


def test_engagements_billed_by_hand_without_invoice_are_not_reviewed() -> None:
    engagement = _engagement("old", (0, 1))
    session = engagement.sessions[0].model_copy(
        update={"description": SessionDescription(invoice="2020-02-001")}
    )
    [billed_by_hand], _ = group_sessions([session])

    plan = plan_links([billed_by_hand], [])

    assert plan.reviews == ()


def _with_invoice(engagement: Engagement, number: str) -> Engagement:
    root = engagement.sessions[0].model_copy(
        update={"description": SessionDescription(invoice=number)}
    )
    return engagement.model_copy(update={"sessions": (root, *engagement.sessions[1:])})


def test_description_names_the_invoice() -> None:
    # Morning and afternoon groups on the same days, billed apart.
    morning = _with_invoice(_engagement("morning", (0, 2)), "B")
    afternoon = _with_invoice(_engagement("afternoon", (0, 2)), "A")

    # Days sharing, not equal: the description decides, and says so.
    plan = plan_links([morning, afternoon], [_invoice("A", 0), _invoice("B", 0)])

    assert [
        (link.engagement.id, link.invoice.document_number, link.how)
        for link in plan.links
    ] == [
        ("morning", "B", "description"),
        ("afternoon", "A", "description"),
    ]
    assert plan.reviews == plan.unmatched == ()


def test_one_invoice_for_engagements_billed_by_hand() -> None:
    # Morning and afternoon groups billed together: not linkable one to one.
    morning = _with_invoice(_engagement("morning", (0, 2)), "A")
    afternoon = _with_invoice(_engagement("afternoon", (0, 2)), "A")

    plan = plan_links([morning, afternoon], [_invoice("A", 0, 1)])

    assert plan.links == plan.reviews == ()
    assert plan.unmatched == ()


def test_description_naming_no_candidate_is_ignored() -> None:
    # A typo in the hand-written number (2O21 for 2021): days decide.
    engagement = _with_invoice(_engagement("e", (0, 2)), "2O21-11-003")

    plan = plan_links([engagement], [_invoice("2021-11-005", 0, 1)])

    assert [(link.invoice.document_number, link.how) for link in plan.links] == [
        ("2021-11-005", "exact")
    ]
