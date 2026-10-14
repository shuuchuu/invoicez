import csv
from datetime import date
from io import StringIO
from pathlib import Path
from shutil import copytree
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZipFile

from git import Repo
from pydantic import ValidationError
from pytest import approx, fixture, raises

from invoicez.ledger.settings import LedgerSettings
from invoicez.ledger.spreadsheet import read_sheets
from invoicez.ledger.taxes import contribution_rate, income_tax, to_euros

DATA_DIR = Path(__file__).parent / "test_cli"

# 2025 income, per share.
_BRACKETS: dict[float, float] = {11600: 0.11, 29579: 0.30, 84577: 0.41, 181917: 0.45}


def _cell(value: Any) -> str:
    if value is None:
        return "<table:table-cell/>"
    if isinstance(value, date):
        return (
            f'<table:table-cell office:value-type="date" '
            f'office:date-value="{value.isoformat()}"/>'
        )
    if isinstance(value, int | float):
        return (
            f'<table:table-cell office:value-type="float" office:value="{value}">'
            f"<text:p>{value}</text:p></table:table-cell>"
        )
    return (
        f'<table:table-cell office:value-type="string">'
        f"<text:p>{escape(value)}</text:p></table:table-cell>"
    )


def write_ods(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    """Write a minimal OpenDocument spreadsheet, rows given as lists of values."""
    tables = "".join(
        f'<table:table table:name="{name}">'
        + "".join(
            f"<table:table-row>{''.join(map(_cell, row))}</table:table-row>"
            for row in rows
        )
        # As editors save them: empty rows up to the end of the sheet.
        + '<table:table-row table:number-rows-repeated="1048000">'
        '<table:table-cell table:number-columns-repeated="16384"/></table:table-row>'
        "</table:table>"
        for name, rows in sheets.items()
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?><office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:spreadsheet>{tables}</office:spreadsheet>"
        "</office:body></office:document-content>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", content)


def test_income_tax_follows_the_progressive_scale() -> None:
    # 69 499.28 of turnover, 34 % allowance.
    assert income_tax(45869.52, _BRACKETS, 1) == approx(6864.85, abs=0.01)
    assert income_tax(11000, _BRACKETS, 1) == 0
    assert income_tax(200000, _BRACKETS, 1) == approx(
        0.11 * (29579 - 11600)
        + 0.30 * (84577 - 29579)
        + 0.41 * (181917 - 84577)
        + 0.45 * (200000 - 181917)
    )
    # Two shares: twice the tax on half the income.
    assert income_tax(45869.52, _BRACKETS, 2) == approx(
        2 * income_tax(45869.52 / 2, _BRACKETS, 1)
    )


def test_contribution_rate_in_force() -> None:
    rates = {date(2025, 1, 1): 0.248, date(2026, 1, 1): 0.258}
    assert contribution_rate(rates, date(2024, 12, 31)) is None
    assert contribution_rate(rates, date(2025, 12, 31)) == approx(0.248)
    assert contribution_rate(rates, date(2026, 1, 1)) == approx(0.258)


def test_to_euros_rounds_half_away_from_zero() -> None:
    assert to_euros(438.5) == 439
    assert to_euros(-106.5) == -107
    assert to_euros(0.49) == 0


def test_pivot_date_needs_a_spreadsheet() -> None:
    with raises(ValidationError, match="pivot_date needs a spreadsheet"):
        LedgerSettings(pivot_date=date(2026, 9, 1))


def test_read_sheets(tmp_path: Path) -> None:
    path = tmp_path / "ledger.ods"
    write_ods(
        path,
        {
            "2025": [
                ["Facture", "HT", "Envoyée", None],
                ["2025-01-001", 1770, date(2025, 1, 18)],
                [],
                [None, 1770],
            ]
        },
    )

    assert read_sheets(path) == {
        "2025": [
            {"Facture": "2025-01-001", "HT": 1770.0, "Envoyée": date(2025, 1, 18)},
            {"Facture": None, "HT": 1770.0},
        ]
    }


def test_read_sheets_refuses_other_files(tmp_path: Path) -> None:
    from invoicez.exceptions import InvoicezError

    path = tmp_path / "ledger.ods"
    path.write_text("not a spreadsheet")
    with raises(InvoicezError, match="not a readable OpenDocument spreadsheet"):
        read_sheets(path)


@fixture
def working_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    tmp_dir = tmp_path / "data"
    copytree(DATA_DIR, tmp_dir)
    Repo.init(str(tmp_dir))
    monkeypatch.chdir(tmp_dir)
    # Before the pivot date, 2020-10-001 is read from the spreadsheet; after it,
    # 2020-10-002, the credit note 2020-10-003 cancelling it, and the Tricatel
    # invoice of 2026 are read from the documents.
    with (tmp_dir / "settings.yml").open("a", encoding="utf8") as settings:
        settings.write(
            "ledger:\n"
            "  pivot_date: 2020-10-10\n"
            "  spreadsheet: ledger.ods\n"
            "  contribution_rates: {2020-01-01: 0.2}\n"
            "  income_tax:\n"
            "    brackets: {2020: {1000: 0.1}}\n"
        )
    header = ["Facture", "Entreprise", "Formation", "Jours", "Stagiaires"]
    header += ["Principal HT", "Frais HT", "TVA", "Envoyée", "Payée"]
    write_ods(
        tmp_dir / "ledger.ods",
        {
            "2020": [
                header,
                [
                    *("2020-10-001", "Company", "BDA", 2, 8, 1500, 300, 0),
                    *(date(2020, 10, 7), date(2020, 11, 2)),
                ],
                # Dated after the pivot by its document: read from the latter.
                ["2020-10-002", "Company 2", "XYZ", 3, 4, 1620, 0, 0],
                # Totals.
                [None, None, None, 5, 12, 3120, 300, 0],
            ],
            "Notes": [["Facture"], ["not a year"]],
        },
    )
    return tmp_dir


def _ledger(monkeypatch: Any, sent: list[dict[str, Any]], *args: str) -> None:
    from invoicez.cli import ledger, main

    monkeypatch.setenv("COLUMNS", "250")
    monkeypatch.setattr(ledger, "list_sent_invoices", lambda: sent)
    with raises(SystemExit) as exit_info:
        main("ledger", *args)
    assert not exit_info.value.code


def _rows(output: str) -> dict[str, list[str]]:
    rows = [
        [cell.strip() for cell in line.split("│")[1:-1]]
        for line in output.splitlines()
        if line.startswith("│")
    ]
    return {row[0]: row for row in rows}


_SENT = [
    {
        "id": 1,
        "en_invoice": {
            "number": "2020-10-002",
            "issue_date": "2020-10-15",
            "totals": {"total_without_vat": "1600.00"},
        },
        "events": [
            {"id": 1, "created_at": "2020-10-15T10:00:00Z", "status_code": "fr:201"},
            {"id": 2, "created_at": "2020-11-20T10:00:00Z", "status_code": "fr:212"},
        ],
    }
]


def test_ledger(working_dir: Path, monkeypatch: Any, capsys: Any) -> None:
    _ledger(monkeypatch, _SENT, "2020")

    output = capsys.readouterr().out
    rows = _rows(output)
    # Invoice, Date, Company, Reference, Days, Trainees, Excl. VAT, Expenses, VAT,
    # Contrib., Income tax, Net, Sent, Paid.
    assert rows["2020-10-001"] == [
        "2020-10-001", "2020-10-07", "Company", "BDA", "2", "8", "1800.00", "300.00",
        "0.00", "360", "19", "1421.00", "2020-10-07", "2020-11-02",
    ]  # fmt: skip
    # Taxed at 10 % on what the invoice adds above 1 000 of taxable income.
    assert rows["2020-10-002"] == [
        "2020-10-002", "2020-10-15", "Entreprise client", "", "0", "", "1620.00",
        "0.00", "324.00", "324", "107", "1189.00", "2020-10-15", "2020-11-20",
    ]  # fmt: skip
    assert rows["2020-10-003"][4:12] == [
        "0", "", "-1620.00", "0.00", "-324.00", "-324", "-107", "-1189.00",
    ]  # fmt: skip
    assert rows["Total"][4:11] == ["2", "8", "1800.00", "300.00", "0.00", "360", "19"]
    assert "2026" not in rows
    assert "3 invoices, 2 training days, 8 trainees" in output
    assert "(1 invoices without trainees)" in output
    assert "collected during the year: 3420.00" in output
    assert "2020-10-002: no trainees in its document" in output
    assert "1620.00 excluding VAT in its document, but 1600.00 sent" in output


def test_ledger_without_settings_reads_documents(
    working_dir: Path, monkeypatch: Any, capsys: Any
) -> None:
    settings = working_dir / "settings.yml"
    settings.write_text(settings.read_text().split("ledger:")[0])
    _ledger(monkeypatch, [], "2020", "2026")

    rows = _rows(capsys.readouterr().out)
    # Its unit is not DAY: no training days.
    assert rows["2020-10-001"][4:10] == ["0", "", "1800.00", "300.00", "0.00", ""]
    # Read after the credit note 2020-10-003, but still an invoice.
    assert rows["BURGERQUEEN-TRICATEL-0001"][4:9] == [
        "0",
        "",
        "1620.00",
        "0.00",
        "0.00",
    ]


def test_credit_notes_cancel_days_and_trainees(
    working_dir: Path, monkeypatch: Any, capsys: Any
) -> None:
    invoice = working_dir / "company2/2020-10-002-Entreprise client.yml"
    invoice.write_text(
        invoice.read_text(encoding="utf8")
        .replace("Facture de truc machin", "Journée de formation")
        .replace('unit: "jours"', 'unit: "DAY"')
        + "trainees: 4\n",
        encoding="utf8",
    )
    _ledger(monkeypatch, [], "2020")

    rows = _rows(capsys.readouterr().out)
    assert rows["2020-10-002"][4:7] == ["3", "4", "1620.00"]
    # Its lines read "Avoir pour « Journée de formation »".
    assert rows["2020-10-003"][4:7] == ["-3", "-4", "-1620.00"]
    assert rows["Total"][4:6] == ["2", "8"]


def test_ledger_csv(working_dir: Path, monkeypatch: Any, capsys: Any) -> None:
    _ledger(monkeypatch, _SENT, "2020", "--csv")

    rows = list(csv.DictReader(StringIO(capsys.readouterr().out)))
    assert [row["number"] for row in rows] == [
        "2020-10-001",
        "2020-10-002",
        "2020-10-003",
    ]
    assert rows[0]["source"] == "spreadsheet"
    assert rows[1] | {"net": "1189.00", "paid": "2020-11-20"} == rows[1]
    assert rows[1]["source"] == "documents"
