from datetime import date
from pathlib import Path
from shutil import copy
from typing import Any

from git import Repo
from pydantic import ValidationError
from pytest import mark, raises
from yaml import safe_dump, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.settings import GcalSettings
from invoicez.migration.config import migrate_config_mapping, migrate_gcal_settings
from invoicez.migration.documents import (
    DocumentMigrationError,
    migrate_document_mapping,
)
from invoicez.variables import Variables

_OLD_CONFIG: dict[str, Any] = {
    "font_size": "11pt",
    "name_template": "Facture-{invoice_number}-{company}-{ref}",
    "title_pattern": "some pattern",
    "company": {"maximum_days": 30},
    "tva": 0.2,
    "user": {
        "name": "Hugo Mougard",
        "email": "hugo@mougard.fr",
        "phone": "+33 6 37 63 82 71",
        "place": "Nantes",
        "address": "16 rue Alain Barbe Torte\n44200 Nantes",
        "ape": "8559A",
        "siret": "829 167 196 00023",
        "tva_number": "FR~38~829~167~196",
        "nda": "52441050944",
        "bic": "CMBRFR2BXXX",
        "iban": "FR76 1558 9297 2408 5819 2444 164",
        "rib": "15589 29724 08581924441 64",
        "account_bank": "CCM LANNILIS",
        "account_owner": "M MOUGARD HUGO / 16 RUE ALAIN BARBE TORTE / 44200 NANTES",
    },
    "companies": {
        "orsys": {
            "name": "Orsys",
            "address": "La Grande Arche paroi Nord\n92800 PUTEAUX",
        },
        "sparks": {
            "name": "Sparks Formation",
            "siren": "485 281 570",
            "address": "96 bd Vivier Merle\n69003 LYON",
            "maximum_days": 45,
        },
    },
    "rates": {"orsys": {"trainings": {}}},
    "trainings": {"bda": {"name": "Big Data Analytics", "company": "orsys"}},
}


def test_migrate_config_mapping_seller() -> None:
    new, warnings = migrate_config_mapping(_OLD_CONFIG)

    user = new["user"]
    assert user["siret"] == "82916719600023"
    assert user["siren"] == "829167196"
    assert user["iban"] == _OLD_CONFIG["user"]["iban"].replace(" ", "")
    assert user["address"] == "16 rue Alain Barbe Torte"
    assert user["city"] == "Nantes"
    assert user["postcode"] == "44200"
    assert user["country"] == "FR"
    assert user["account_name"] == _OLD_CONFIG["user"]["account_owner"]
    assert user["electronic_address"] == "TODO_ELECTRONIC_ADDRESS"
    assert user["electronic_address_scheme"] == "TODO_ELECTRONIC_ADDRESS_SCHEME"
    assert "place" not in new["latex_extras"]
    assert "rib" not in new["latex_extras"]
    assert "account_bank" not in new["latex_extras"]
    assert new["latex_extras"] == {"font_size": "11pt", "ape": "8559A"}
    assert any("tva_number" in w for w in warnings)


def test_migrate_config_mapping_companies() -> None:
    new, _ = migrate_config_mapping(_OLD_CONFIG)

    orsys = new["companies"]["orsys"]
    assert orsys["siren"] == "482761160"
    assert orsys["reference_kind"] == "order"
    assert orsys["city"] == "PUTEAUX"
    assert orsys["postcode"] == "92800"

    sparks = new["companies"]["sparks"]
    assert sparks["siren"] == "485281570"
    assert sparks["reference_kind"] == "contract"
    assert sparks["maximum_days"] == 45


def test_migrate_config_mapping_tva_and_invoice_meta() -> None:
    new, _ = migrate_config_mapping(_OLD_CONFIG)

    assert new["tva"] == {"category": "S", "rate": 0.2}
    assert new["invoice_meta"]["type_invoice"] == "S5"
    assert new["invoice_meta"]["spec_format"] == "urn:cen.eu:en16931:2017"
    assert new["invoice_meta"]["currency"] == "EUR"


def test_migrate_config_mapping_notices() -> None:
    new, _ = migrate_config_mapping(_OLD_CONFIG)

    assert new["notices"]["legal"] == "NDA : 52441050944"
    assert new["notices"]["general"] == "TODO_GENERAL_NOTICE"
    assert "recouvrement" in new["notices"]
    assert "taux_retard" in new["notices"]
    assert "escompte" in new["notices"]


def test_migrate_config_mapping_drops_non_variables_keys() -> None:
    new, warnings = migrate_config_mapping(_OLD_CONFIG)

    for key in ("rates", "trainings", "name_template", "title_pattern"):
        assert key not in new
    assert any("rates" in w and "trainings" in w for w in warnings)


def test_migrate_config_mapping_missing_tva_is_omitted_with_warning() -> None:
    old = {**_OLD_CONFIG, "tva": None}
    new, warnings = migrate_config_mapping(old)

    assert "tva" not in new
    assert any(w.startswith("tva:") for w in warnings)


def test_migrated_mapping_is_structurally_valid_variables() -> None:
    new, _ = migrate_config_mapping(_OLD_CONFIG)

    invoice_mapping = {
        "document_number": "2020-10-001",
        "date": "2020-10-07",
        "company": "orsys",
        "products": [
            {
                "description": "Journée de formation",
                "pu": 750,
                "n": 2,
                "unit": "jours",
            }
        ],
    }

    variables = Variables.from_mappings(new, invoice_mapping)
    assert variables.user.siren == "829167196"
    assert variables.company.name == "Orsys"


_OLD_GCAL_CONFIG: dict[str, Any] = {
    "title_pattern": "(?P<multipart>->)?(?P<company>.*) - (?P<training>.*) - "
    "(?P<place>.*)",
    "rates": {
        "orsys": {
            "trainings": {
                "default": {"training_day": 690, "preparation_day": 500},
                "t2": {"training_day": 690, "preparation_day": 500},
            },
            "places": {"paris": 220},
        },
        "sparks": {
            "trainings": {
                "default": {"training_day": 690, "preparation_day": 500},
                "t2": {"training_day": 750, "preparation_day": 500},
            },
            "places": None,
        },
    },
    "trainings": {
        "bda": {"name": "Big Data Analytics", "company": "orsys"},
        "drn": {"name": "Deep Learning", "company": "orsys", "rate": "t2"},
        "pav": {"name": "Python avancé", "company": "sparks", "rate": "t2"},
        "ml1": {"name": "Machine Learning", "company": "eni"},
    },
}


def test_migrate_gcal_settings_title_pattern() -> None:
    section, warnings = migrate_gcal_settings(_OLD_GCAL_CONFIG)

    assert section["title_pattern"].startswith("(?P<continuation>->)?")
    assert any("'continuation'" in w for w in warnings)


def test_migrate_gcal_settings_rates() -> None:
    section, warnings = migrate_gcal_settings(_OLD_GCAL_CONFIG)

    # default is the same in every company, t2 is not.
    assert section["rates"] == {
        "default": {"day": 690, "preparation_day": 500},
        "orsys-t2": {"day": 690, "preparation_day": 500},
        "sparks-t2": {"day": 750, "preparation_day": 500},
    }
    assert any("rates.orsys.places: dropped (paris: 220)" in w for w in warnings)


def test_migrate_gcal_settings_trainings() -> None:
    section, warnings = migrate_gcal_settings(_OLD_GCAL_CONFIG)

    assert section["trainings"] == {
        "orsys": {
            # No rate in the old config: no implied "default" either.
            "bda": {"name": "Big Data Analytics", "rate": "TODO"},
            "drn": {"name": "Deep Learning", "rate": "orsys-t2"},
        },
        "sparks": {"pav": {"name": "Python avancé", "rate": "sparks-t2"}},
        "eni": {"ml1": {"name": "Machine Learning", "rate": "TODO"}},
    }
    assert any(w.startswith("trainings.bda.rate: missing") for w in warnings)
    assert any(w.startswith("trainings.ml1.rate:") for w in warnings)


def test_migrated_gcal_settings_are_valid_once_filled() -> None:
    old = {
        **_OLD_GCAL_CONFIG,
        "trainings": dict(list(_OLD_GCAL_CONFIG["trainings"].items())[:3]),
    }
    section, warnings = migrate_gcal_settings(old)

    assert section["start_date"] == "TODO"
    assert any(w.startswith("start_date:") for w in warnings)
    assert any(w.startswith("templates:") for w in warnings)
    section["start_date"] = "2026-10-01"
    section["templates"] = dict.fromkeys(section["templates"], "x")
    with raises(ValidationError, match=r"orsys\.bda: 'TODO'"):
        GcalSettings.model_validate(section)
    section["trainings"]["orsys"]["bda"]["rate"] = "default"
    settings = GcalSettings.model_validate(section)
    assert settings.trainings["orsys"]["drn"].rate == "orsys-t2"


def test_migrate_config_gcal_cli(tmp_path: Path, capsys: Any) -> None:
    from invoicez.cli import main

    old_config = tmp_path / "config.yml"
    old_config.write_text(safe_dump(_OLD_GCAL_CONFIG), encoding="utf8")

    with raises(SystemExit):
        main("migrate-config", str(old_config), "--gcal")

    output = capsys.readouterr().out
    assert output.startswith("# TODO: ")
    assert safe_load(output)["gcal"]["rates"]["default"]["day"] == 690
    assert not (tmp_path / "variables.yml").exists()


_COMPANIES = {"Orsys": "orsys", "Sparks Formation": "sparks"}
_FILE_NAME = "Facture-2021-01-001-Orsys-BDAZZ1.yml"


def _migrate(
    data: dict[str, Any], file_name: str = _FILE_NAME
) -> tuple[dict[str, Any], list[str]]:
    return migrate_document_mapping(data, file_name=file_name, companies=_COMPANIES)


def _legacy_document(**overrides: Any) -> dict[str, Any]:
    return {
        "company": "orsys",
        "date": "13/01/2021",
        "invoice_number": "2021-01-001",
        "description": "Facture",
        "products": [
            {
                "date": "11--12/01/2021",
                "description": "Journée de formation",
                "n": 2,
                "pu": 750,
            },
        ],
        "ref": "BDAZZ1",
        **overrides,
    }


@mark.parametrize(
    "old_key,new_key,value,expected",
    [
        ("invoice_number", "document_number", "2021-01-001", "2021-01-001"),
        ("ref", "reference", "BDAZZ1", "BDAZZ1"),
        ("limit_date", "payment_due_date", "12/02/2021", date(2021, 2, 12)),
    ],
)
def test_migrate_document_renames(
    old_key: str, new_key: str, value: str, expected: Any
) -> None:
    new, _ = _migrate(_legacy_document(**{old_key: value}))

    assert old_key not in new
    assert new[new_key] == expected


def test_migrate_document_rename_conflict_is_an_error() -> None:
    with raises(DocumentMigrationError, match="'ref' and 'reference'"):
        _migrate(_legacy_document(reference="OTHER"))


@mark.parametrize(
    "value,expected",
    [
        ("7 octobre 2020", date(2020, 10, 7)),
        ("25/11/2020", date(2020, 11, 25)),
        ("9/2/2022", date(2022, 2, 9)),
        ("2026-09-29", date(2026, 9, 29)),
        (date(2026, 9, 17), date(2026, 9, 17)),
    ],
)
def test_migrate_document_dates(value: Any, expected: date) -> None:
    new, _ = _migrate(_legacy_document(date=value))

    assert new["date"] == expected


def test_migrate_document_unrecognised_date_is_an_error() -> None:
    with raises(DocumentMigrationError, match="unrecognised date"):
        _migrate(_legacy_document(date="DATE"))


def test_migrate_document_number_and_company_from_file_name() -> None:
    data = _legacy_document()
    del data["invoice_number"], data["company"]

    new, warnings = _migrate(data, "Facture-2020-10-001-Sparks Formation-CF1.yml")

    assert new["document_number"] == "2020-10-001"
    assert new["company"] == "sparks"
    assert any(w.startswith("company:") for w in warnings)


def test_migrate_document_unknown_company_is_a_warning() -> None:
    data = _legacy_document()
    del data["company"]

    new, warnings = _migrate(data, "Facture-2021-01-001-Unknown-X.yml")

    assert "company" not in new
    assert "company: missing, and not found in the file name" in warnings


def test_migrate_document_credit_note() -> None:
    data = _legacy_document(cancels="2020-12-001", limit_date="12/02/2021")

    new, warnings = _migrate(data)

    assert new == {
        "document_number": "2021-01-001",
        "date": date(2021, 1, 13),
        "cancels": "2020-12-001",
    }
    assert any("credit note" in w for w in warnings)


@mark.parametrize(
    "line_date,n,expected",
    [
        ("21--22/09/2020", 2, [((2020, 9, 21), (2020, 9, 22), 2)]),
        ("4–6/6/2025", 3, [((2025, 6, 4), (2025, 6, 6), 3)]),  # ruff: ignore[ambiguous-unicode-character-string]
        ("30/11--2/12/2022", 3, [((2022, 11, 30), (2022, 12, 2), 3)]),
        ("30/11/2020-02/12/2020", 3, [((2020, 11, 30), (2020, 12, 2), 3)]),
        ("13/01/2021", 1, [((2021, 1, 13), None, 1)]),
        (
            "10,16,17,20/3/2023",
            4,
            [
                ((2023, 3, 10), None, 1),
                ((2023, 3, 16), (2023, 3, 17), 2),
                ((2023, 3, 20), None, 1),
            ],
        ),
        (
            "24–27/6/2025\\\\30/6/2025\\\\1/7/2025",  # ruff: ignore[ambiguous-unicode-character-string]
            6,
            [((2025, 6, 24), (2025, 6, 27), 4), ((2025, 6, 30), (2025, 7, 1), 2)],
        ),
        (
            "14\\&16/10/2024",
            2,
            [((2024, 10, 14), None, 1), ((2024, 10, 16), None, 1)],
        ),
        (
            "17/05/2021, 19/05/2021-21/05/2021",
            4,
            [((2021, 5, 17), None, 1), ((2021, 5, 19), (2021, 5, 21), 3)],
        ),
    ],
)
def test_migrate_document_line_dates(
    line_date: str,
    n: int,
    expected: list[tuple[tuple[int, int, int], tuple[int, int, int] | None, int]],
) -> None:
    product = {"date": line_date, "description": "Journée", "n": n, "pu": 750}

    new, _ = _migrate(_legacy_document(products=[product]))

    assert [(p["start"], p.get("end"), p["n"]) for p in new["products"]] == [
        (date(*start), date(*end) if end else None, line_n)
        for start, end, line_n in expected
    ]
    assert sum(p["n"] * p["pu"] for p in new["products"]) == n * 750


def test_migrate_document_half_days_become_half_a_day() -> None:
    product = {"description": "Demi-journée de formation BDA", "n": 3, "pu": 375}

    new, _ = _migrate(_legacy_document(products=[product]))

    assert new["products"] == [
        {"description": "Journée de formation BDA", "n": 1.5, "pu": 750, "unit": "DAY"}
    ]


@mark.parametrize(
    "description,expected",
    [
        ("Demi-jour de formation", "Jour de formation"),
        ("Formation en demi-journée", "Formation en journée"),
        ("Formation en DEMI-JOURNÉE", "Formation en JOURNÉE"),
    ],
)
def test_migrate_document_half_day_labels(description: str, expected: str) -> None:
    product = {"description": description, "n": 2, "pu": 375}

    new, _ = _migrate(_legacy_document(products=[product]))

    assert new["products"][0]["description"] == expected


def test_migrate_document_half_day_split() -> None:
    product = {"date": "4,6/11/2020", "description": "Demi-journée", "n": 2, "pu": 375}

    new, _ = _migrate(_legacy_document(products=[product]))

    assert [(p["n"], p["pu"], p["unit"]) for p in new["products"]] == [
        (0.5, 750, "DAY"),
        (0.5, 750, "DAY"),
    ]


def test_migrate_document_lump_sum_over_several_spans_is_an_error() -> None:
    product = {"date": "4,6/11/2020", "description": "Frais", "n": 1, "pu": 100}

    with raises(DocumentMigrationError, match="cannot be split"):
        _migrate(_legacy_document(products=[product]))


@mark.parametrize(
    "description,unit",
    [
        ("Journée de formation", "DAY"),
        ("Jour de formation", "DAY"),
        ("Forfait de frais de déplacement journalier", "DAY"),
        ("Forfait quotidien de frais de déplacement", "DAY"),
        ("Demi-journée de formation", "DAY"),
        ("Demi-jour de formation", "DAY"),
        ("Frais de déplacement sur justificatifs", "LS"),
        ("Frais de déjeuner sur justificatif", "LS"),
        ("QCM de 5 questions MLB", "LS"),
    ],
)
def test_migrate_document_unit_inference(description: str, unit: str) -> None:
    product = {"description": description, "n": 2, "pu": 100}

    new, warnings = _migrate(_legacy_document(products=[product]))

    assert new["products"][0]["unit"] == unit
    assert f"products[0].unit: inferred {unit!r}" in warnings


def test_migrate_document_keeps_existing_unit() -> None:
    product = {"description": "Frais", "n": 1, "pu": 100, "unit": "DAY"}

    new, warnings = _migrate(_legacy_document(products=[product]))

    assert new["products"] == [product]
    assert not any("unit" in w for w in warnings)


@mark.parametrize(
    "data",
    [
        _legacy_document(),
        _legacy_document(cancels="2020-12-001"),
        _legacy_document(
            products=[
                {"date": "10,16,17/3/2023", "description": "Jour", "n": 3, "pu": 1}
            ]
        ),
    ],
)
def test_migrate_document_is_idempotent(data: dict[str, Any]) -> None:
    once, _ = _migrate(data)
    twice, warnings = _migrate(once)

    assert twice == once
    assert warnings == []


def test_migrated_document_is_valid_variables() -> None:
    global_mapping, _ = migrate_config_mapping(_OLD_CONFIG)
    data = _legacy_document(
        limit_date="12/02/2021",
        products=[
            {"date": "11,12,14/01/2021", "description": "Journée", "n": 3, "pu": 750},
            {"description": "Frais de déplacement", "n": 1, "pu": 80},
        ],
    )

    new, _ = _migrate(data)

    variables = Variables.from_mappings(global_mapping, new)
    assert variables.document_number == "2021-01-001"
    assert variables.due_date == date(2021, 2, 12)
    assert len(variables.products) == 3


def test_migrate_documents_cli(tmp_path: Path, monkeypatch: Any) -> None:
    from invoicez.cli import main

    copy(Path(__file__).parent / "test_cli" / "variables.yml", tmp_path)
    Repo.init(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    legacy = _legacy_document(company="company1")
    document = tmp_path / "2021" / _FILE_NAME
    document.parent.mkdir()
    document.write_text(safe_dump(legacy, allow_unicode=True), encoding="utf8")
    template = tmp_path / "invoice.yml"
    template.write_text(safe_dump({"date": "DATE", "products": []}), encoding="utf8")

    with raises(SystemExit):
        main("migrate-documents")
    assert safe_load(document.read_text(encoding="utf8")) == legacy

    with raises(SystemExit):
        main("migrate-documents", "--write")
    migrated = safe_load(document.read_text(encoding="utf8"))
    assert migrated["document_number"] == "2021-01-001"
    assert migrated["products"][0]["unit"] == "DAY"
    assert safe_load(template.read_text(encoding="utf8"))["date"] == "DATE"

    broken = tmp_path / "Facture-2021-01-002-Orsys-X.yml"
    broken.write_text(safe_dump({"date": "DATE", "products": []}), encoding="utf8")
    with raises(InvoicezError, match="1 document"):
        main("migrate-documents")
