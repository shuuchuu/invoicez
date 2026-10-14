import re
from typing import Any

# Dropped entirely: not part of the Variables schema (extra="forbid"), so
# they cannot live in the migrated variables.yml. rates, trainings and
# title_pattern are migrated to settings.yml by migrate_gcal_settings.
_DROPPED_TOP_LEVEL_KEYS = ("rates", "trainings", "name_template", "title_pattern")

_TODO = "TODO"

# Companies known to invoice against a contract (BT-12) rather than the
# default purchase order (BT-13).
_REFERENCE_KIND_OVERRIDES = {"sparks": "contract"}

# Orsys's siren, dropped in an in-progress manual edit of config.yml;
# restored here since it's still needed for a valid Seller/Company record.
_ORSYS_SIREN = "482761160"

_ELECTRONIC_ADDRESS_PLACEHOLDER = "TODO_ELECTRONIC_ADDRESS"
_ELECTRONIC_ADDRESS_SCHEME_PLACEHOLDER = "TODO_ELECTRONIC_ADDRESS_SCHEME"

_ADDRESS_LAST_LINE_RE = re.compile(r"^(?P<postcode>\d{5})\s+(?P<city>.+)$")

# Split out of the paragraph hardcoded in ../invoices/templates/jinja2/main.tex,
# to preserve continuity with what's actually been printed on real invoices.
_NOTICE_RECOUVREMENT = (
    "En cas de retard de paiement, seront exigibles, conformément à "
    "l'article L 441-6 du code de commerce, une indemnité forfaitaire pour "
    "frais de recouvrement de 40 €."
)
_NOTICE_TAUX_RETARD = (
    "En cas de retard de paiement, seront exigibles, conformément à "
    "l'article L 441-6 du code de commerce, une indemnité calculée sur la "
    "base de trois fois le taux de l'intérêt légal en vigueur."
)
# No historical equivalent in the old template; standard mandatory mention.
_NOTICE_ESCOMPTE = (
    "Les réglements reçus avant la date d'échéance ne donneront pas lieu à escompte."
)


def _strip(value: str) -> str:
    return value.replace(" ", "")


def _split_address(block: str, path: str, warnings: list[str]) -> dict[str, str]:
    lines = block.strip().splitlines()
    match = _ADDRESS_LAST_LINE_RE.match(lines[-1].strip()) if lines else None
    if match is None:
        warnings.append(
            f"{path}: could not split '<postcode> <city>' out of the address "
            "block; left city/postcode empty, address kept as-is."
        )
        return {"address": block.strip(), "city": "", "postcode": "", "country": "FR"}
    return {
        "address": "\n".join(lines[:-1]),
        "postcode": match.group("postcode"),
        "city": match.group("city"),
        "country": "FR",
    }


def _migrate_seller(old_user: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    siret = _strip(str(old_user["siret"]))
    seller = {
        "name": old_user["name"],
        "email": old_user["email"],
        "phone": old_user["phone"],
        "bic": old_user["bic"],
        "iban": _strip(old_user["iban"]),
        "siret": siret,
        "siren": siret[:9],
        "account_name": old_user["account_owner"],
        "electronic_address": _ELECTRONIC_ADDRESS_PLACEHOLDER,
        "electronic_address_scheme": _ELECTRONIC_ADDRESS_SCHEME_PLACEHOLDER,
        **_split_address(old_user["address"], "user.address", warnings),
    }
    warnings.append("user.electronic_address/electronic_address_scheme: TODO")
    warnings.append(
        "user.account_name: copied verbatim from old user.account_owner — "
        "verify it reads naturally as an account holder name."
    )
    if "tva_number" in old_user:
        warnings.append(
            "user.tva_number: dropped — the VAT id is now computed from "
            "siren by the XML converter (_compute_vat_id). Nothing "
            "currently exposes that computed id back to the PDF template "
            "(known gap, not fixed by this migration)."
        )
    return seller


def _migrate_companies(
    old_companies: dict[str, Any], warnings: list[str]
) -> dict[str, Any]:
    companies: dict[str, Any] = {}
    for key, old_company in old_companies.items():
        siren = old_company.get("siren")
        if siren is not None:
            siren = _strip(str(siren))
        elif key == "orsys":
            siren = _ORSYS_SIREN
            warnings.append(
                "companies.orsys.siren: restored to 482761160 (dropped in "
                "an in-progress manual edit of config.yml)."
            )
        else:
            siren = ""
            warnings.append(f"companies.{key}.siren: missing in old config.yml.")

        address_path = f"companies.{key}.address"
        company = {
            "name": old_company["name"],
            "siren": siren,
            "electronic_address": _ELECTRONIC_ADDRESS_PLACEHOLDER,
            "electronic_address_scheme": _ELECTRONIC_ADDRESS_SCHEME_PLACEHOLDER,
            "reference_kind": _REFERENCE_KIND_OVERRIDES.get(key, "order"),
            **_split_address(old_company["address"], address_path, warnings),
        }
        if "maximum_days" in old_company:
            company["maximum_days"] = old_company["maximum_days"]
        companies[key] = company
        warnings.append(f"companies.{key}.electronic_address/_scheme: TODO")
    return companies


def _migrate_tva(old_tva: Any, warnings: list[str]) -> dict[str, Any] | None:
    if old_tva is None:
        warnings.append(
            "tva: missing from old config.yml — Variables.tva is required, "
            "so this needs a real decision. Omitted from the output."
        )
        return None
    return {"category": "S", "rate": float(old_tva)}


def _notices(old_user: dict[str, Any], warnings: list[str]) -> dict[str, str]:
    warnings.append(
        "notices.escompte: no historical equivalent — added the standard "
        "mandatory mention, please review."
    )
    warnings.append("notices.general: TODO — depends on VAT situation per invoice.")
    return {
        "recouvrement": _NOTICE_RECOUVREMENT,
        "taux_retard": _NOTICE_TAUX_RETARD,
        "escompte": _NOTICE_ESCOMPTE,
        "legal": f"NDA : {old_user['nda']}",
        "general": "TODO_GENERAL_NOTICE",
    }


def _invoice_meta(warnings: list[str]) -> dict[str, str]:
    warnings.append(
        "invoice_meta.type_invoice: defaulted to 'S5' (sous-traitance "
        "service) — the old config.yml has no equivalent field. Review "
        "per invoice type; the alternative is 'S1' (service)."
    )
    return {
        "spec_format": "urn:cen.eu:en16931:2017",
        "currency": "EUR",
        "type_invoice": "S5",
    }


def migrate_config_mapping(old: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Transform an old-format config.yml mapping into a variables.yml one.

    Converts the flat, pre-e-invoicing config.yml shape into the current
    variables.yml mapping expected by invoicez.variables.Variables.

    Returns:
        The new mapping, plus a list of human-readable warnings about
        everything that could not be derived automatically and needs review.
    """
    warnings: list[str] = []

    new: dict[str, Any] = {
        "invoice_meta": _invoice_meta(warnings),
        "company": {"maximum_days": old["company"]["maximum_days"]},
        "user": _migrate_seller(old["user"], warnings),
        "latex_extras": {
            "font_size": old["font_size"],
            "ape": old["user"]["ape"],
        },
        "notices": _notices(old["user"], warnings),
        "companies": _migrate_companies(old.get("companies", {}), warnings),
    }

    tva = _migrate_tva(old.get("tva"), warnings)
    if tva is not None:
        new["tva"] = tva

    dropped = [key for key in _DROPPED_TOP_LEVEL_KEYS if key in old]
    if dropped:
        warnings.append(
            f"{', '.join(dropped)}: not part of the Variables schema "
            '(extra="forbid"), so they were not carried over. Migrate rates, '
            "trainings and title_pattern to settings.yml with "
            "`invoicez migrate-config --gcal`; name_template is replaced by "
            "invoice_template in settings.yml."
        )

    return new, warnings


def _migrate_rates(
    old_rates: dict[str, Any], warnings: list[str]
) -> tuple[dict[str, Any], dict[tuple[str, str], str]]:
    """Flatten per-company rate tiers into named rates.

    A tier with the same values in every company that defines it keeps its
    name; otherwise each company gets its own "<company>-<tier>" rate.

    Returns:
        The rates, and the rate name of each (company, tier).
    """
    tiers: dict[str, dict[str, dict[str, Any]]] = {}
    for company, company_rates in old_rates.items():
        for tier, rate in (company_rates.get("trainings") or {}).items():
            new_rate = {"day": rate["training_day"]}
            if "preparation_day" in rate:
                new_rate["preparation_day"] = rate["preparation_day"]
            tiers.setdefault(tier, {})[company] = new_rate
        if places := company_rates.get("places"):
            fees = ", ".join(f"{place}: {fee}" for place, fee in places.items())
            warnings.append(
                f"rates.{company}.places: dropped ({fees}); add travel lines by "
                "hand to the drafted documents."
            )

    rates: dict[str, Any] = {}
    names: dict[tuple[str, str], str] = {}
    for tier, by_company in tiers.items():
        shared = len({tuple(sorted(rate.items())) for rate in by_company.values()}) == 1
        for company, rate in by_company.items():
            name = tier if shared else f"{company}-{tier}"
            rates[name] = rate
            names[company, tier] = name
    return rates, names


def _migrate_trainings(
    old_trainings: dict[str, Any],
    rate_names: dict[tuple[str, str], str],
    warnings: list[str],
) -> dict[str, Any]:
    trainings: dict[str, Any] = {}
    for token, old_training in old_trainings.items():
        company = old_training["company"]
        tier = old_training.get("rate")
        # The old config implied the "default" tier when rate was missing;
        # settings.yml has no fallback rate, so the choice is left to the user.
        rate = _TODO if tier is None else rate_names.get((company, tier), _TODO)
        if tier is None:
            warnings.append(
                f"trainings.{token}.rate: missing (the old config implied "
                "'default'); pick one explicitly."
            )
        elif rate == _TODO:
            warnings.append(
                f"trainings.{token}.rate: no rate {tier!r} for company {company!r}."
            )
        trainings.setdefault(company, {})[token] = {
            "name": old_training["name"],
            "rate": rate,
        }
    return trainings


def migrate_gcal_settings(old: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Build the gcal section of settings.yml from an old-format config.yml.

    Returns:
        The gcal section, plus a list of human-readable warnings about
        everything that could not be derived automatically and needs review.
    """
    warnings: list[str] = []

    title_pattern = old["title_pattern"].replace("(?P<multipart>", "(?P<continuation>")
    if title_pattern != old["title_pattern"]:
        warnings.append("title_pattern: group 'multipart' renamed 'continuation'.")
    rates, rate_names = _migrate_rates(old.get("rates") or {}, warnings)
    trainings = _migrate_trainings(old.get("trainings") or {}, rate_names, warnings)

    warnings.append(
        "start_date: to fill; engagements ending before it are ignored (decision D5)."
    )
    warnings.append(
        "templates: to fill, as Jinja strings for the document description and "
        "the day, half-day and preparation lines."
    )
    return {
        "start_date": _TODO,
        "title_pattern": title_pattern,
        "templates": {
            "description": _TODO,
            "day_line": _TODO,
            "half_day_line": _TODO,
            "preparation_line": _TODO,
        },
        "rates": rates,
        "trainings": trainings,
    }, warnings
