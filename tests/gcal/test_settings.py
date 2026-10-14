from typing import Any

from pydantic import ValidationError
from pytest import fixture, mark, raises

from invoicez.exceptions import InvoicezError
from invoicez.gcal.settings import GcalSettings, check_against_variables
from invoicez.settings import Settings

_TITLE_PATTERN = r"""
    ^
    (?P<continuation> -> \s+ )?
    (?P<company> .*? ) \s+ - \s+
    (?P<training> .*? ) \s+ - \s+
    (?P<place> .*? )
    (?: \s+ - \s+ (?P<extra> .*? ) )?
    $
"""


@fixture
def gcal_mapping() -> dict[str, Any]:
    return {
        "start_date": "2026-10-01",
        "title_pattern": _TITLE_PATTERN,
        "templates": {
            "description": "Facture pour {{ training.name }}",
            "day_line": "Journée de formation",
            "half_day_line": "Demi-journée de formation",
            "preparation_line": "Journée de préparation",
        },
        "rates": {"default": {"day": 690, "preparation_day": 500}, "t1": {"day": 590}},
        "trainings": {
            "orsys": {"bda": {"name": "Big Data Analytics", "rate": "default"}},
            "sparks": {"pav": {"name": "Python avancé", "rate": "t1"}},
        },
    }


def test_valid_settings(gcal_mapping: dict[str, Any]) -> None:
    settings = Settings.model_validate(
        {"quote_template": "q", "invoice_template": "i", "gcal": gcal_mapping}
    )

    assert settings.gcal is not None
    assert settings.gcal.unit == "DAY"
    assert settings.gcal.output_dir == "gcal-ymls/{year}"
    assert settings.gcal.durations.half_day_hours == (2.5, 4.0)
    assert settings.gcal.trainings["sparks"]["pav"].rate == "t1"


def test_settings_without_gcal() -> None:
    settings = Settings.model_validate({"quote_template": "q", "invoice_template": "i"})

    assert settings.gcal is None


def test_unknown_rate(gcal_mapping: dict[str, Any]) -> None:
    gcal_mapping["trainings"]["orsys"]["bda"]["rate"] = "t9"

    with raises(ValidationError, match=r"unknown rate: orsys\.bda: 't9'"):
        GcalSettings.model_validate(gcal_mapping)


@mark.parametrize(
    "pattern,message",
    [
        (r"(?P<company>.*) - (?P<training>.*)", "lacks the groups continuation, place"),
        (r"(?P<company>", "does not compile"),
    ],
)
def test_bad_title_pattern(
    gcal_mapping: dict[str, Any], pattern: str, message: str
) -> None:
    gcal_mapping["title_pattern"] = pattern

    with raises(ValidationError, match=message):
        GcalSettings.model_validate(gcal_mapping)


@mark.parametrize(
    "durations",
    [
        {"half_day_hours": (4.0, 2.5)},
        {"half_day_hours": (2.5, 6.0), "day_hours": (6.0, 9.0)},
    ],
)
def test_bad_durations(gcal_mapping: dict[str, Any], durations: dict[str, Any]) -> None:
    gcal_mapping["durations"] = durations

    with raises(ValidationError):
        GcalSettings.model_validate(gcal_mapping)


def test_check_against_variables(gcal_mapping: dict[str, Any]) -> None:
    settings = GcalSettings.model_validate(gcal_mapping)

    check_against_variables(settings, {"companies": {"orsys": {}, "sparks": {}}})
    with raises(InvoicezError, match=r"missing from variables\.yml: sparks"):
        check_against_variables(settings, {"companies": {"orsys": {}}})
