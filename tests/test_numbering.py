from datetime import date
from pathlib import Path
from typing import Any

from pytest import raises
from yaml import safe_dump

from invoicez.exceptions import InvoicezError
from invoicez.numbering import get_next_document_number

_TODAY = date(2026, 10, 14)


def _write(directory: Path, name: str, **content: Any) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(safe_dump(content), encoding="utf8")
    return path


def _next(directory: Path, *, quote: bool = False) -> str:
    return get_next_document_number(directory, quote=quote, today=_TODAY)


def test_first_of_the_month(tmp_path: Path) -> None:
    _write(tmp_path, "a.yml", document_number="2026-09-004")

    assert _next(tmp_path) == "2026-10-001"


def test_gap_uses_max_plus_one(tmp_path: Path) -> None:
    _write(tmp_path, "a.yml", document_number="2026-10-001")
    _write(tmp_path, "sub/b.yml", document_number="2026-10-003")

    assert _next(tmp_path) == "2026-10-004"


def test_deleted_file_does_not_reuse_a_number(tmp_path: Path) -> None:
    for number in (1, 2, 3):
        _write(tmp_path, f"{number}.yml", document_number=f"2026-10-00{number}")
    (tmp_path / "2.yml").unlink()

    assert _next(tmp_path) == "2026-10-004"


def test_quotes_and_invoices_are_numbered_separately(tmp_path: Path) -> None:
    _write(tmp_path, "invoice.yml", document_number="2026-10-002")
    _write(tmp_path, "credit-note.yml", document_number="2026-10-003", cancels="x")
    _write(tmp_path, "quote.yml", document_number="2026-10-001", quote=True)

    assert _next(tmp_path) == "2026-10-004"
    assert _next(tmp_path, quote=True) == "2026-10-002"


def test_ignores_non_documents(tmp_path: Path) -> None:
    (tmp_path / "empty.yml").write_text("", encoding="utf8")
    _write(tmp_path, "variables.yml", companies={})
    _write(tmp_path, "legacy.yml", invoice_number="2026-10-005")

    assert _next(tmp_path) == "2026-10-001"


def test_duplicate_number_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "a.yml", document_number="2026-10-001")
    _write(tmp_path, "b.yml", document_number="2026-10-001")
    _write(tmp_path, "quote.yml", document_number="2026-10-001", quote=True)

    with raises(InvoicezError, match="share the document number 2026-10-001"):
        _next(tmp_path)
