import builtins
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from pytest import fixture, raises
from yaml import safe_dump, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.loading import analyse, load_events, load_gcal
from invoicez.gcal.reconcile import scan_documents
from invoicez.gcal.settings import GcalSettings

ASSETS_DIR = Path(__file__).parent / "assets"
_VARIABLES = Path(__file__).parents[1] / "test_cli" / "variables.yml"


@fixture
def data_dir(tmp_path: Path, gcal_settings: GcalSettings) -> Path:
    # A data repository whose companies are the fixture's.
    variables = safe_load(_VARIABLES.read_text(encoding="utf8"))
    company = variables["companies"]["company1"]
    variables["companies"] = {
        key: {**company, "name": key.capitalize()} for key in gcal_settings.trainings
    }
    (tmp_path / "variables.yml").write_text(safe_dump(variables), encoding="utf8")
    settings = safe_load((ASSETS_DIR / "settings.yml").read_text(encoding="utf8"))
    settings |= {"quote_template": "q", "invoice_template": "i"}
    (tmp_path / "settings.yml").write_text(
        safe_dump(settings, allow_unicode=True), encoding="utf8"
    )
    return tmp_path


def _rewrite_settings(data_dir: Path, **changes: Any) -> None:
    path = data_dir / "settings.yml"
    settings = safe_load(path.read_text(encoding="utf8"))
    for key, value in changes.items():
        if value is None:
            del settings[key]
        else:
            settings[key] = value
    path.write_text(safe_dump(settings, allow_unicode=True), encoding="utf8")


def test_load_gcal(data_dir: Path) -> None:
    context = load_gcal(data_dir / "settings.yml", data_dir / "variables.yml")

    assert context.settings.invoice_template == "i"
    assert context.gcal.start_date == date(2020, 1, 1)
    assert "company_0" in context.global_mapping["companies"]


def test_load_gcal_without_section(data_dir: Path) -> None:
    _rewrite_settings(data_dir, gcal=None)

    with raises(InvoicezError, match="has no gcal section"):
        load_gcal(data_dir / "settings.yml", data_dir / "variables.yml")


def test_load_gcal_invalid(data_dir: Path) -> None:
    _rewrite_settings(data_dir, unknown_key=1)

    with raises(InvoicezError, match=r"settings\.yml is invalid"):
        load_gcal(data_dir / "settings.yml", data_dir / "variables.yml")


def test_load_gcal_unknown_company(data_dir: Path) -> None:
    variables = safe_load((data_dir / "variables.yml").read_text(encoding="utf8"))
    del variables["companies"]["company_5"]
    (data_dir / "variables.yml").write_text(safe_dump(variables), encoding="utf8")

    with raises(InvoicezError, match="company_5"):
        load_gcal(data_dir / "settings.yml", data_dir / "variables.yml")


def test_load_events_from_file(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings
) -> None:
    events = load_events(gcal_settings, events_file=ASSETS_DIR / "events.json")

    assert events == raw_events


def test_load_events_from_a_file_that_is_not_a_list(
    gcal_settings: GcalSettings, tmp_path: Path
) -> None:
    (tmp_path / "events.json").write_text("{}", encoding="utf8")

    with raises(InvoicezError, match="JSON list"):
        load_events(gcal_settings, events_file=tmp_path / "events.json")


def test_load_events_without_calendar_id(gcal_settings: GcalSettings) -> None:
    with raises(InvoicezError, match="invoicez gcal calendars"):
        load_events(gcal_settings)


def test_load_events_without_the_extra(
    gcal_settings: GcalSettings, monkeypatch: Any
) -> None:
    real_import = builtins.__import__

    def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "invoicez.gcal" and "google" in (args[2] or ()):
            msg = "No module named 'googleapiclient'"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    settings = gcal_settings.model_copy(update={"calendar_id": "calendar-id"})

    with raises(InvoicezError, match=r"pip install invoicez\[gcal\]"):
        load_events(settings)


def test_load_events_from_google(gcal_settings: GcalSettings, monkeypatch: Any) -> None:
    from invoicez.gcal import google

    calls: list[Any] = []
    monkeypatch.setattr(google, "credentials", lambda: "creds")
    monkeypatch.setattr(google, "calendar_service", lambda creds: f"service({creds})")
    monkeypatch.setattr(
        google,
        "fetch_events",
        lambda service, calendar_id: calls.append((service, calendar_id)) or [],
    )
    settings = gcal_settings.model_copy(update={"calendar_id": "calendar-id"})

    assert load_events(settings) == []
    # The whole calendar: no time_min.
    assert calls == [("service(creds)", "calendar-id")]


def test_analyse(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings, tmp_path: Path
) -> None:
    settings = gcal_settings.model_copy(update={"start_date": date(2026, 10, 10)})

    analysis = analyse(
        raw_events, settings, scan_documents(tmp_path), today=date(2026, 10, 14)
    )

    assert Counter(s.classification.state for s in analysis.engagements) == {
        "before_cutoff": 117,
        "billed_by_hand": 27,
        "upcoming": 1,
        "tentative": 11,
        "unbilled": 1,
    }
    priced = [s for s in analysis.engagements if s.pricing is not None]
    assert {s.classification.state for s in priced} == {
        "unbilled",
        "upcoming",
        "tentative",
    }
    assert len(priced) == 13
    assert Counter(d.level for d in analysis.diagnostics) == {"warning": 2, "info": 2}
    assert analysis.orphans == ()


def test_analyse_only_prices_what_may_be_drafted(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings, tmp_path: Path
) -> None:
    # Past trainings may leave the settings without errors.
    company_0 = gcal_settings.trainings["company_0"]
    settings = gcal_settings.model_copy(
        update={
            "start_date": date(2026, 10, 10),
            "trainings": {
                **gcal_settings.trainings,
                "company_0": {k: v for k, v in company_0.items() if k != "training_0"},
            },
        }
    )

    analysis = analyse(
        raw_events, settings, scan_documents(tmp_path), today=date(2026, 10, 14)
    )

    assert [d for s in analysis.engagements for d in s.diagnostics] == []


def test_analyse_reports_missing_rates_before_billing_day(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings, tmp_path: Path
) -> None:
    trainings = gcal_settings.trainings
    settings = gcal_settings.model_copy(
        update={
            "start_date": date(2026, 10, 10),
            "trainings": {
                **trainings,
                "company_0": {
                    k: v
                    for k, v in trainings["company_0"].items()
                    if k != "training_11"
                },
                "company_1": {
                    k: v for k, v in trainings["company_1"].items() if k != "training_1"
                },
            },
        }
    )

    analysis = analyse(
        raw_events, settings, scan_documents(tmp_path), today=date(2026, 10, 14)
    )

    errors = [
        (s.classification.state, d.level)
        for s in analysis.engagements
        for d in s.diagnostics
    ]
    assert errors == [("upcoming", "error")] + [("tentative", "error")] * 7


def test_analyse_warns_about_ended_tentative_engagements(
    raw_events: list[dict[str, Any]], gcal_settings: GcalSettings, tmp_path: Path
) -> None:
    # From 2020, the two '? Company_0' bookings of September 2026 are over.
    analysis = analyse(
        raw_events, gcal_settings, scan_documents(tmp_path), today=date(2026, 10, 14)
    )

    warnings = [
        (str(s.engagement.end), d.message.split(": ", 1)[1])
        for s in analysis.engagements
        for d in s.diagnostics
    ]
    assert warnings == [
        (
            "2026-09-25",
            "unconfirmed booking over since 2026-09-25: remove its '?' to bill it, "
            "or delete it",
        ),
        (
            "2026-10-02",
            "unconfirmed booking over since 2026-10-02: remove its '?' to bill it, "
            "or delete it",
        ),
    ]
