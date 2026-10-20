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
from yaml import safe_dump, safe_load

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
    # The credit note goes with the invoice it cancels, paid.
    assert "2020: sent" not in output
    # Label, Days, Trainees, Excl. VAT, Contrib., Income tax, Net.
    assert rows["Paid"] == ["Paid", "2", "8", "1800.00", "360", "19", "1421.00"]
    assert rows["Sent"] == ["Sent", "0", "0", "0.00", "0", "0", "0.00"]
    assert rows["Total"] == ["Total", "2", "8", "1800.00", "360", "19", "1421.00"]
    assert "2026" not in rows
    assert "2020: 3 invoices (1 without trainees)" in output
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
    assert rows["Total"][1:3] == ["2", "8"]


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


_GCAL_DIR = Path(__file__).parent / "gcal" / "assets"
_EVENTS = str(_GCAL_DIR / "events.json")


@fixture
def calendar_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    from invoicez.cli import ledger

    # The calendar of the gcal tests, on 2026-10-14: Id_355 is over and not
    # billed, Id_357 confirmed and to come, Id_359, Id_363 and Id_364 tentative
    # in 2026, others tentative in 2027.
    gcal = safe_load((_GCAL_DIR / "settings.yml").read_text(encoding="utf8"))["gcal"]
    variables = safe_load((DATA_DIR / "variables.yml").read_text(encoding="utf8"))
    company = variables["companies"]["company1"]
    variables["companies"] = {
        key: {**company, "name": key.capitalize()} for key in gcal["trainings"]
    }
    (tmp_path / "variables.yml").write_text(safe_dump(variables), encoding="utf8")
    gcal["start_date"] = date(2026, 10, 10)
    settings = {
        "quote_template": "{document_number}",
        "invoice_template": "{document_number}",
        "gcal": gcal,
        "ledger": {
            "contribution_rates": {date(2026, 1, 1): 0.2},
            "income_tax": {"brackets": {2026: {10000: 0.1}}},
        },
    }
    (tmp_path / "settings.yml").write_text(
        safe_dump(settings, allow_unicode=True), encoding="utf8"
    )
    # An invoice for Id_354, which is then not forecast whatever its state.
    (tmp_path / "2026-10-001.yml").write_text(
        safe_dump(
            {
                "document_number": "2026-10-001",
                "date": "2026-10-01",
                "company": "company_0",
                "trainees": 6,
                "products": [
                    {
                        "n": 9,
                        "pu": 1000,
                        "unit": "DAY",
                        "description": "Journée de formation",
                    }
                ],
                "source": {
                    "kind": "gcal",
                    "calendar_id": "calendar-id",
                    "event_ids": ["Id_354"],
                    "fingerprint": "0" * 64,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf8",
    )
    Repo.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ledger, "_today", lambda: date(2026, 10, 14))
    return tmp_path


def _tables(output: str) -> list[list[list[str]]]:
    # The rows of each table, in order, its header first.
    tables: list[list[list[str]]] = []
    for line in output.splitlines():
        if line.startswith("┏"):
            tables.append([])
        elif line.startswith(("│", "┃")):
            tables[-1].append([cell.strip() for cell in line[1:-1].split(line[0])])
    return tables


def test_ledger_forecast(calendar_dir: Path, monkeypatch: Any, capsys: Any) -> None:
    _ledger(monkeypatch, [], "--forecast", "--events", _EVENTS, "2026", "2027")

    output = capsys.readouterr().out
    # Neither sent nor paid: the 2026 paid table is left out.
    sent, to_bill, coming, totals, *later = _tables(output)
    assert "2026: sent" in output
    assert [row[0] for row in sent] == ["Invoice", "2026-10-001"]
    assert "2026: 1 invoices;" in output
    # Date, Company, Reference, Days, Trainees, Excl. VAT, VAT, Contrib.,
    # Income tax, Net. Billed on the day it ends, taxed after the invoices.
    assert to_bill[1:] == [
        [
            *("2026-10-14", "Company_0", "", "3", "", "2070.00", "0.00"),
            *("414", "0", "1656.00"),
        ]
    ]
    assert "Over but not invoiced: invoicez gcal bill." in output
    assert [row[:1] + row[5:] for row in coming[1:]] == [
        ["2026-11-06", "5000.00", "0.00", "1000", "61", "3939.00"],
        ["2026-11-26", "4000.00", "0.00", "800", "264", "2936.00"],
        ["2026-12-11", "3000.00", "0.00", "600", "198", "2202.00"],
        ["2026-12-18", "3000.00", "0.00", "600", "198", "2202.00"],
    ]
    assert totals == [
        ["", "Days", "Trainees", "Excl. VAT", "Contrib.", "Income tax", "Net"],
        ["Paid", "0", "0", "0.00", "0", "0", "0.00"],
        ["Sent", "9", "6", "9000.00", "1800", "0", "7200.00"],
        ["To bill", "3", "0", "2070.00", "414", "0", "1656.00"],
        ["Sure", "5", "0", "5000.00", "1000", "61", "3939.00"],
        ["Tentative", "10", "0", "10000.00", "2000", "660", "7340.00"],
        ["Total", "27", "6", "26070.00", "5214", "721", "20135.00"],
    ]
    # Only tentative engagements in 2027.
    coming_2027, totals_2027 = later
    assert len(coming_2027) == 9
    assert [row[0] for row in totals_2027[1:]] == [
        *("Paid", "Sent", "Tentative", "Total"),
    ]


def test_ledger_forecast_csv(calendar_dir: Path, monkeypatch: Any, capsys: Any) -> None:
    _ledger(monkeypatch, [], "--forecast", "--events", _EVENTS, "--csv")

    rows = list(csv.DictReader(StringIO(capsys.readouterr().out)))
    assert [(row["number"], row["source"], row["forecast"]) for row in rows] == [
        ("2026-10-001", "documents", ""),
        ("Id_355", "calendar", "to_bill"),
        ("Id_357", "calendar", "sure"),
        ("Id_359", "calendar", "tentative"),
        ("Id_363", "calendar", "tentative"),
        ("Id_364", "calendar", "tentative"),
    ]


def test_ledger_forecast_leaves_past_years_alone(
    calendar_dir: Path, monkeypatch: Any, capsys: Any
) -> None:
    # The calendar is not even read.
    _ledger(monkeypatch, [], "--forecast", "--events", "missing.json", "2025")

    output = capsys.readouterr().out
    assert "Total" in output
    assert "with " not in output


def test_forecasts_are_taxed_after_more_certain_entries() -> None:
    from invoicez.ledger.entries import Entry
    from invoicez.ledger.report import ledger_lines
    from invoicez.ledger.settings import IncomeTax

    def entry(day: int, forecast: Any) -> Entry:
        return Entry(
            number=str(day), date=date(2026, 11, day), company="C", reference=None,
            days=1, trainees=None, principal=1000, expenses=0, vat=0, sent=None,
            paid=None, credit_note=False, forecast=forecast,
            source="documents" if forecast is None else "calendar",
        )  # fmt: skip

    settings = LedgerSettings(
        income_tax=IncomeTax(allowance=0, brackets={2026: {1000: 0.1, 2000: 0.2}})
    )
    lines = ledger_lines(
        [entry(1, "tentative"), entry(2, "sure"), entry(3, None)], settings
    )
    # Taxed invoice, sure, then tentative, but kept in order of date.
    assert [line.entry.number for line in lines] == ["1", "2", "3"]
    assert [line.income_tax for line in lines] == [200, 100, 0]
