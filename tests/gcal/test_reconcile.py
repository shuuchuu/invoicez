from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pytest import mark, raises
from yaml import safe_dump

from invoicez.exceptions import InvoicezError
from invoicez.gcal.grouping import Engagement, group_sessions
from invoicez.gcal.parsing import Session, SessionDescription, parse_events
from invoicez.gcal.reconcile import (
    DocumentIndex,
    classify,
    orphaned_documents,
    scan_documents,
)
from invoicez.gcal.settings import GcalSettings

_TODAY = date(2026, 10, 14)
_START_DATE = date(2026, 9, 1)


def _engagement(
    *event_days: int, legacy_invoice: str | None = None, preparation_days: float = 0
) -> Engagement:
    sessions = [
        Session(
            event_id=f"event_{day}",
            html_link="link",
            title="Orsys - BDA - cv",
            continuation=i > 0,
            company="Orsys",
            training="BDA",
            place="cv",
            extra=None,
            start=_TODAY + timedelta(days=day),
            end=_TODAY + timedelta(days=day),
            kind="day",
            quantity=1,
            description=SessionDescription.model_validate(
                {"invoice": legacy_invoice, "preparation_days": preparation_days}
                if i == 0
                else {}
            ),
        )
        for i, day in enumerate(event_days)
    ]
    [engagement], _ = group_sessions(sessions)
    return engagement


def _write(git_dir: Path, name: str, content: Any) -> Path:
    path = git_dir / "gcal-ymls" / "2026" / f"{name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(safe_dump(content, allow_unicode=True), encoding="utf8")
    return path


def _invoice(git_dir: Path, number: str, engagement: Engagement, **extra: Any) -> Path:
    return _write(
        git_dir,
        f"Facture-{number}",
        {
            "document_number": number,
            "date": "2026-10-14",
            "company": "orsys",
            "products": [],
            "source": {
                "kind": "gcal",
                "calendar_id": "calendar-id",
                "event_ids": [s.event_id for s in engagement.sessions],
                "fingerprint": engagement.fingerprint,
            },
            **extra,
        },
    )


def _state(engagement: Engagement, index: DocumentIndex) -> tuple[str, tuple[str, ...]]:
    classification = classify(engagement, index, today=_TODAY, start_date=_START_DATE)
    return classification.state, classification.documents


def test_scan_documents(tmp_path: Path) -> None:
    engagement = _engagement(-7, -6)
    gcal = _invoice(tmp_path, "2026-10-004", engagement)
    _write(tmp_path, "by-hand", {"document_number": "2026-10-001", "products": []})
    _write(
        tmp_path,
        "credit-note",
        {"document_number": "2026-10-005", "cancels": "2026-10-001"},
    )
    # Quotes are numbered apart and never billed.
    _write(tmp_path, "quote", {"document_number": "2026-10-004", "quote": True})
    _write(tmp_path, "settings", {"gcal": {"start_date": "2026-09-01"}})
    (tmp_path / "empty.yml").write_text("", encoding="utf8")

    index = scan_documents(tmp_path)

    assert list(index.documents) == ["2026-10-004"]
    assert index.documents["2026-10-004"].path == gcal
    assert index.documents["2026-10-004"].source.fingerprint == engagement.fingerprint
    assert index.by_event == {
        "event_-7": ("2026-10-004",),
        "event_-6": ("2026-10-004",),
    }
    assert index.cancelled == {"2026-10-001"}


@mark.parametrize(
    ("content", "message"),
    [
        ("document_number: [", "not valid YAML"),
        (
            "document_number: 2026-10-002\nsource: {kind: gcal}",
            "invalid source",
        ),
    ],
)
def test_scan_documents_errors(tmp_path: Path, content: str, message: str) -> None:
    (tmp_path / "doc.yml").write_text(content, encoding="utf8")

    with raises(InvoicezError, match=message):
        scan_documents(tmp_path)


def test_scan_documents_duplicate_numbers(tmp_path: Path) -> None:
    _write(tmp_path, "a", {"document_number": "2026-10-001"})
    _write(tmp_path, "b", {"document_number": "2026-10-001"})

    with raises(InvoicezError, match="share the number 2026-10-001"):
        scan_documents(tmp_path)


def test_unbilled_states(tmp_path: Path) -> None:
    index = scan_documents(tmp_path)

    assert _state(_engagement(-60), index) == ("before_cutoff", ())
    # Ends on start_date: still billed from the calendar.
    assert _state(_engagement(-43), index) == ("unbilled", ())
    assert _state(_engagement(-44), index) == ("before_cutoff", ())
    assert _state(_engagement(-7, -6), index) == ("unbilled", ())
    # Billable on its last day.
    assert _state(_engagement(-1, 0), index) == ("unbilled", ())
    assert _state(_engagement(-1, 1), index) == ("upcoming", ())
    assert _state(_engagement(-7, legacy_invoice="2026-09-003"), index) == (
        "billed_by_hand",
        (),
    )


def test_billed(tmp_path: Path) -> None:
    engagement = _engagement(-7, -6)
    _invoice(tmp_path, "2026-10-004", engagement)

    assert _state(engagement, scan_documents(tmp_path)) == ("billed", ("2026-10-004",))


def test_billed_before_cutoff_stays_billed(tmp_path: Path) -> None:
    engagement = _engagement(-60)
    _invoice(tmp_path, "2026-08-001", engagement)

    assert _state(engagement, scan_documents(tmp_path))[0] == "billed"


@mark.parametrize(
    "changed",
    [
        _engagement(-7, -5),
        _engagement(-7),
        _engagement(-7, -6, -5),
        _engagement(-7, -6, preparation_days=1),
    ],
)
def test_changed_since_billing(tmp_path: Path, changed: Engagement) -> None:
    _invoice(tmp_path, "2026-10-004", _engagement(-7, -6))

    assert _state(changed, scan_documents(tmp_path)) == (
        "changed_since_billing",
        ("2026-10-004",),
    )


def test_billed_then_cancelled(tmp_path: Path) -> None:
    engagement = _engagement(-7, -6)
    _invoice(tmp_path, "2026-10-004", engagement)
    _write(
        tmp_path, "credit", {"document_number": "2026-10-005", "cancels": "2026-10-004"}
    )

    assert _state(engagement, scan_documents(tmp_path)) == (
        "billed_then_cancelled",
        ("2026-10-004",),
    )


def test_billed_again_after_cancellation(tmp_path: Path) -> None:
    engagement = _engagement(-7, -6)
    _invoice(tmp_path, "2026-10-004", engagement)
    _write(
        tmp_path, "credit", {"document_number": "2026-10-005", "cancels": "2026-10-004"}
    )
    _invoice(tmp_path, "2026-10-006", engagement)

    assert _state(engagement, scan_documents(tmp_path)) == ("billed", ("2026-10-006",))


def test_billed_twice(tmp_path: Path) -> None:
    engagement = _engagement(-7, -6)
    _invoice(tmp_path, "2026-10-004", _engagement(-7))
    _invoice(tmp_path, "2026-10-006", engagement)

    assert _state(engagement, scan_documents(tmp_path)) == (
        "billed_twice",
        ("2026-10-004", "2026-10-006"),
    )


def test_orphaned_documents(tmp_path: Path) -> None:
    kept = _engagement(-7, -6)
    _invoice(tmp_path, "2026-10-001", kept)
    _invoice(tmp_path, "2026-10-002", _engagement(-20, -19))
    _invoice(tmp_path, "2026-10-003", _engagement(-30))
    _write(
        tmp_path, "credit", {"document_number": "2026-10-004", "cancels": "2026-10-003"}
    )
    # event_-19 was deleted from the calendar.
    shrunk = _engagement(-20)

    orphans = orphaned_documents(scan_documents(tmp_path), [kept, shrunk])

    assert [(o.document.document_number, o.missing_event_ids) for o in orphans] == [
        ("2026-10-002", ("event_-19",)),
    ]


def test_fixture(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings, tmp_path: Path
) -> None:
    engagements, _ = group_sessions(parse_events(raw_events, gcal_settings)[0])
    index = scan_documents(tmp_path)

    def states(start_date: date) -> Counter[str]:
        return Counter(
            classify(e, index, today=_TODAY, start_date=start_date).state
            for e in engagements
        )

    # 27 engagements carry a hand-written invoice number; 12 are not over.
    assert states(gcal_settings.start_date) == {
        "unbilled": 118,
        "billed_by_hand": 27,
        "upcoming": 12,
    }
    # With the data repo's start_date, only the engagement ending today is
    # left to bill.
    assert states(_TODAY) == {
        "before_cutoff": 117,
        "billed_by_hand": 27,
        "upcoming": 12,
        "unbilled": 1,
    }
    assert orphaned_documents(index, engagements) == []
