from typing import Any

from invoicez.migration.config import migrate_config_mapping
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
