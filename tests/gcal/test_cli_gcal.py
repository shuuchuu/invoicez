from datetime import date
from pathlib import Path
from typing import Any

from git import Repo
from pytest import fixture, raises
from yaml import safe_dump, safe_load

from invoicez.cli import gcal as gcal_cli
from invoicez.cli import main
from invoicez.exceptions import InvoicezError
from invoicez.gcal import google
from invoicez.gcal.grouping import group_sessions
from invoicez.gcal.parsing import parse_events
from invoicez.gcal.settings import GcalSettings
from invoicez.variables import Variables

ASSETS_DIR = Path(__file__).parent / "assets"
EVENTS = str(ASSETS_DIR / "events.json")
_VARIABLES = Path(__file__).parents[1] / "test_cli" / "variables.yml"
# The engagement ending on _TODAY, the only one to bill after _START_DATE.
_UNBILLED_ID = "Id_355"
# The next one, from 2026-11-02 to 2026-11-06, without a ref either.
_NEXT_ID = "Id_357"


@fixture
def data_dir(tmp_path: Path, gcal_settings: GcalSettings, monkeypatch: Any) -> Path:
    variables = safe_load(_VARIABLES.read_text(encoding="utf8"))
    company = variables["companies"]["company1"]
    variables["companies"] = {
        key: {**company, "name": key.capitalize()} for key in gcal_settings.trainings
    }
    (tmp_path / "variables.yml").write_text(safe_dump(variables), encoding="utf8")
    settings = safe_load((ASSETS_DIR / "settings.yml").read_text(encoding="utf8"))
    settings["gcal"]["start_date"] = date(2026, 10, 10)
    settings["gcal"]["calendar_id"] = "calendar-id"
    settings |= {
        "quote_template": "Devis-{document_number}-{company}-{name}",
        "invoice_template": "Facture-{document_number}-{company}-{name}",
    }
    (tmp_path / "settings.yml").write_text(
        safe_dump(settings, allow_unicode=True), encoding="utf8"
    )
    Repo.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gcal_cli, "_today", lambda: date(2026, 10, 14))
    monkeypatch.setenv("COLUMNS", "300")
    return tmp_path


def _run(*args: str) -> int:
    with raises(SystemExit) as exit_info:
        main("gcal", *args)
    return int(exit_info.value.code or 0)


def _write_invoice(data_dir: Path, number: str, event_ids: list[str]) -> None:
    path = data_dir / "gcal-ymls" / "2026" / f"Facture-{number}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        safe_dump(
            {
                "document_number": number,
                "source": {
                    "kind": "gcal",
                    "calendar_id": "calendar-id",
                    "event_ids": event_ids,
                    "fingerprint": "0" * 64,
                },
            }
        ),
        encoding="utf8",
    )


def test_status(data_dir: Path, capsys: Any) -> None:
    assert _run("status", "--events", EVENTS) == 0

    output = capsys.readouterr().out
    assert output.count("│ unbilled ") == 1
    assert _UNBILLED_ID in output
    assert output.count("│ upcoming ") == 1
    # Unconfirmed bookings, never billed.
    assert output.count("│ tentative ") == 11
    assert "before_cutoff" not in output
    assert "144 other engagements" in output
    # The short events of 2023 and the ignored descriptions.
    assert "4 diagnostics hidden (infos, or events before start_date)" in output


def test_status_all(data_dir: Path, capsys: Any) -> None:
    assert _run("status", "--all", "--events", EVENTS) == 0

    output = capsys.readouterr().out
    assert output.count("│ before_cutoff ") == 117
    assert output.count("│ billed_by_hand ") == 27
    assert output.count("warning: ") == 2
    assert output.count("info: ") == 2
    assert "other engagements" not in output


def test_status_fails_on_errors(data_dir: Path, capsys: Any) -> None:
    settings = safe_load((data_dir / "settings.yml").read_text(encoding="utf8"))
    del settings["gcal"]["trainings"]["company_1"]["training_1"]
    (data_dir / "settings.yml").write_text(
        safe_dump(settings, allow_unicode=True), encoding="utf8"
    )

    assert _run("status", "--events", EVENTS) == 1

    output = capsys.readouterr().out
    # Tentative engagements are priced, so a missing training shows early.
    assert output.count("error: ") == 7
    assert "training 'TRAINING_1' missing from gcal.trainings.company_1" in output


def test_status_fails_on_engagements_billed_twice(
    data_dir: Path, gcal_settings: GcalSettings, capsys: Any
) -> None:
    _write_invoice(data_dir, "2026-10-004", [_UNBILLED_ID])
    _write_invoice(data_dir, "2026-10-005", [_UNBILLED_ID])

    assert _run("status", "--events", EVENTS) == 1

    output = capsys.readouterr().out
    assert "│ billed_twice " in output
    assert "2026-10-004, 2026-10-005" in output


def test_status_reports_orphaned_documents(data_dir: Path, capsys: Any) -> None:
    _write_invoice(data_dir, "2026-09-001", ["deleted-event"])

    assert _run("status", "--events", EVENTS) == 0

    output = capsys.readouterr().out
    assert "orphaned: 2026-09-001 (gcal-ymls/2026/Facture-2026-09-001.yml)" in output
    assert "deleted-event" in output


def test_status_without_gcal_section(data_dir: Path) -> None:
    settings = safe_load((data_dir / "settings.yml").read_text(encoding="utf8"))
    del settings["gcal"]
    (data_dir / "settings.yml").write_text(safe_dump(settings), encoding="utf8")

    with raises(Exception, match="has no gcal section"):
        main("gcal", "status", "--events", EVENTS)


def test_calendars(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setattr(google, "credentials", lambda: "creds")
    monkeypatch.setattr(google, "calendar_service", lambda creds: "service")
    monkeypatch.setattr(
        google,
        "list_calendars",
        lambda service: [
            {"id": "me@example.com", "summary": "Me", "primary": True},
            {"id": "work@group.calendar.google.com", "summary": "Formations"},
        ],
    )

    assert _run("calendars") == 0

    output = capsys.readouterr().out
    assert "me@example.com" in output
    assert "work@group.calendar.google.com" in output
    assert "Formations" in output


def test_fixture_unbilled_id(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings
) -> None:
    engagements, _ = group_sessions(parse_events(raw_events, gcal_settings)[0])

    [engagement] = [e for e in engagements if e.end == date(2026, 10, 14)]
    assert engagement.id == _UNBILLED_ID


@fixture
def answers(monkeypatch: Any) -> list[str]:
    # Answers to the prompts of bill, consumed in order.
    queue: list[str] = []
    monkeypatch.setattr(gcal_cli, "_ask", lambda prompt: queue.pop(0))
    return queue


def _documents(data_dir: Path) -> list[str]:
    return sorted(
        str(path.relative_to(data_dir)) for path in data_dir.glob("gcal-ymls/**/*.yml")
    )


def test_bill(data_dir: Path, answers: list[str], capsys: Any) -> None:
    answers.extend(["REFAA1", "y"])

    assert _run("bill", "--events", EVENTS) == 0

    path = "gcal-ymls/2026/Facture-2026-10-001-Company_0-REFAA1.yml"
    assert _documents(data_dir) == [path]
    document = safe_load((data_dir / path).read_text(encoding="utf8"))
    assert document["document_number"] == "2026-10-001"
    assert document["date"] == "2026-10-14"
    assert document["reference"] == "REFAA1"
    assert document["source"]["event_ids"] == [_UNBILLED_ID]
    assert document["source"]["calendar_id"] == "calendar-id"
    global_mapping = safe_load((data_dir / "variables.yml").read_text(encoding="utf8"))
    Variables.from_mappings(global_mapping, document)
    output = capsys.readouterr().out
    assert "Total: 2070.00" in output
    assert f"File: {path}" in output
    assert "1 written, 0 skipped, 0 failed." in output

    # Billed now: nothing left.
    assert _run("bill", "--events", EVENTS) == 0
    assert "Nothing to bill." in capsys.readouterr().out
    assert _run("status", "--events", EVENTS) == 0
    assert "│ unbilled " not in capsys.readouterr().out


def test_bill_numbers_at_confirmation(
    data_dir: Path, answers: list[str], monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setattr(gcal_cli, "_today", lambda: date(2026, 11, 10))
    answers.extend(["REFAA1", "n", "REFBB2", "y"])

    assert _run("bill", "--events", EVENTS) == 0

    # The skipped engagement leaves no gap.
    assert _documents(data_dir) == [
        "gcal-ymls/2026/Facture-2026-11-001-Company_0-REFBB2.yml"
    ]
    assert "1 written, 1 skipped, 0 failed." in capsys.readouterr().out


def test_bill_quit_and_empty_reference(
    data_dir: Path, answers: list[str], monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setattr(gcal_cli, "_today", lambda: date(2026, 11, 10))
    answers.extend(["", "REFBB2", "q"])

    assert _run("bill", "--events", EVENTS) == 0

    assert _documents(data_dir) == []
    assert "0 written, 1 skipped, 0 failed." in capsys.readouterr().out


def test_bill_by_id(data_dir: Path, answers: list[str], monkeypatch: Any) -> None:
    monkeypatch.setattr(gcal_cli, "_today", lambda: date(2026, 11, 10))
    answers.extend(["REFBB2", "y"])

    assert _run("bill", _NEXT_ID, "--events", EVENTS) == 0

    assert _documents(data_dir) == [
        "gcal-ymls/2026/Facture-2026-11-001-Company_0-REFBB2.yml"
    ]


def test_bill_refuses_what_is_not_billable(data_dir: Path, answers: list[str]) -> None:
    with raises(InvoicezError) as error:
        main("gcal", "bill", "unknown", _NEXT_ID, "Id_359", "--events", EVENTS)

    assert str(error.value).splitlines() == [
        "unknown: no such engagement",
        f"{_NEXT_ID}: upcoming, not billable",
        "Id_359: tentative, not billable",
    ]
    assert _documents(data_dir) == []


def test_bill_refuses_changed_engagements(data_dir: Path, answers: list[str]) -> None:
    _write_invoice(data_dir, "2026-10-004", [_UNBILLED_ID])

    with raises(InvoicezError, match="invoicez cancel <file>"):
        main("gcal", "bill", _UNBILLED_ID, "--events", EVENTS)


def test_bill_rebill(
    data_dir: Path, gcal_settings: GcalSettings, answers: list[str]
) -> None:
    answers.extend(["REFAA1", "y"])
    _run("bill", "--events", EVENTS)
    [first] = _documents(data_dir)
    credit_note = data_dir / "gcal-ymls" / "2026" / "Avoir.yml"
    credit_note.write_text(
        "document_number: 2026-10-002\ndate: 2026-10-14\ncancels: 2026-10-001\n",
        encoding="utf8",
    )

    with raises(InvoicezError, match="add --rebill"):
        main("gcal", "bill", _UNBILLED_ID, "--events", EVENTS)
    answers.extend(["REFAA1", "y"])
    assert _run("bill", _UNBILLED_ID, "--rebill", "--events", EVENTS) == 0

    assert _documents(data_dir) == [
        "gcal-ymls/2026/Avoir.yml",
        first,
        "gcal-ymls/2026/Facture-2026-10-003-Company_0-REFAA1.yml",
    ]


def test_bill_reports_pricing_errors(
    data_dir: Path, answers: list[str], capsys: Any
) -> None:
    settings = safe_load((data_dir / "settings.yml").read_text(encoding="utf8"))
    del settings["gcal"]["trainings"]["company_0"]["training_6"]
    (data_dir / "settings.yml").write_text(
        safe_dump(settings, allow_unicode=True), encoding="utf8"
    )

    assert _run("bill", "--events", EVENTS) == 1

    output = capsys.readouterr().out
    assert "training 'TRAINING_6' missing from gcal.trainings.company_0" in output
    assert "0 written, 0 skipped, 1 failed." in output
    assert _documents(data_dir) == []


def test_bill_refuses_to_overwrite(
    data_dir: Path, answers: list[str], capsys: Any
) -> None:
    target = data_dir / "gcal-ymls/2026/Facture-2026-10-001-Company_0-REFAA1.yml"
    target.parent.mkdir(parents=True)
    target.write_text("# not a document\n", encoding="utf8")
    answers.append("REFAA1")

    assert _run("bill", "--events", EVENTS) == 1

    assert "already exists" in capsys.readouterr().out
    assert target.read_text(encoding="utf8") == "# not a document\n"


def test_bill_build(data_dir: Path, answers: list[str], monkeypatch: Any) -> None:
    built: list[Path] = []
    monkeypatch.setattr(gcal_cli, "runner_run", lambda path, *args: built.append(path))
    answers.extend(["REFAA1", "y"])

    assert _run("bill", "--build", "--events", EVENTS) == 0

    assert [path.name for path in built] == ["Facture-2026-10-001-Company_0-REFAA1.yml"]


def test_bill_without_calendar_id(data_dir: Path) -> None:
    settings = safe_load((data_dir / "settings.yml").read_text(encoding="utf8"))
    del settings["gcal"]["calendar_id"]
    (data_dir / "settings.yml").write_text(
        safe_dump(settings, allow_unicode=True), encoding="utf8"
    )

    with raises(InvoicezError, match="calendar_id is not set"):
        main("gcal", "bill", "--events", EVENTS)


def test_bill_reports_unescaped_latex(
    data_dir: Path, answers: list[str], capsys: Any
) -> None:
    answers.extend(["CF1 & CF2"])

    assert _run("bill", "--events", EVENTS) == 1

    output = capsys.readouterr().out
    assert "unescaped '&'" in output
    assert "0 written, 0 skipped, 1 failed." in output
    assert _documents(data_dir) == []


def test_link(data_dir: Path, capsys: Any) -> None:
    invoice = data_dir / "gcal-ymls" / "2026" / "Facture-2026-10-003-Company_0-X.yml"
    invoice.parent.mkdir(parents=True)
    text = (
        "document_number: 2026-10-003\n"
        "products:\n"
        "- {start: 2026-10-12, end: 2026-10-14, n: 3, pu: 690, unit: DAY, "
        "description: Jour}\n"
    )
    invoice.write_text(text, encoding="utf8")

    assert _run("link", "--events", EVENTS) == 0
    output = capsys.readouterr().out
    assert "2026-10-003" in output
    assert "│ exact" in output
    assert "Add --write to link." in output
    assert invoice.read_text(encoding="utf8") == text

    assert _run("link", "--write", "--events", EVENTS) == 0
    assert "Linked 1 invoices." in capsys.readouterr().out
    assert invoice.read_text(encoding="utf8").startswith(text + "source:\n")

    _run("status", "--all", "--events", EVENTS)
    output = capsys.readouterr().out
    assert "│ unbilled " not in output
    assert output.count("│ billed ") == 1


def test_link_invalid_pair(data_dir: Path) -> None:
    with raises(InvoicezError, match="expected ENGAGEMENT_ID=DOCUMENT_NUMBER"):
        main("gcal", "link", "--pair", "Id_355", "--events", EVENTS)
