from datetime import date
from pathlib import Path
from typing import Any

from git import Repo
from pytest import fixture, raises
from yaml import safe_dump, safe_load

from invoicez.cli import gcal as gcal_cli
from invoicez.cli import main
from invoicez.gcal import google
from invoicez.gcal.grouping import group_sessions
from invoicez.gcal.parsing import parse_events
from invoicez.gcal.settings import GcalSettings

ASSETS_DIR = Path(__file__).parent / "assets"
EVENTS = str(ASSETS_DIR / "events.json")
_VARIABLES = Path(__file__).parents[1] / "test_cli" / "variables.yml"
# The engagement ending on _TODAY, the only one to bill after _START_DATE.
_UNBILLED_ID = "Id_355"


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
    assert "4 diagnostics about events before start_date" in output


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
