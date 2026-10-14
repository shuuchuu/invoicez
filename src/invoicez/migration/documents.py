import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from invoicez.exceptions import InvoicezError
from invoicez.variables import Variables

_RENAMES = {
    "invoice_number": "document_number",
    "ref": "reference",
    "limit_date": "payment_due_date",
}

# A credit note only keeps what main's resolve_cancellation reads: the rest
# is copied from the cancelled invoice.
_CREDIT_NOTE_KEYS = ("document_number", "date", "cancels")

_ALLOWED_KEYS = {*Variables.model_fields, "cancels"}

_DOCUMENT_NUMBER_RE = re.compile(r"\d{4}-\d{2}-\d{3}")

_FRENCH_MONTHS = {
    month: index
    for index, month in enumerate(
        (
            "janvier",
            "février",
            "mars",
            "avril",
            "mai",
            "juin",
            "juillet",
            "août",
            "septembre",
            "octobre",
            "novembre",
            "décembre",
        ),
        start=1,
    )
}
_FRENCH_DATE_RE = re.compile(r"^(\d{1,2}) (\w+) (\d{4})$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

# Legacy line dates are free LaTeX text: "21--22/09/2020", "4-6/6/2025" (with
# "-", "--" or an en dash), "10,16,17,20/3/2023", "20--21/6/2024\\24--27/6/2024",
# "14\&16/10/2024"...
_ITEM_SEPARATORS_RE = re.compile(r"\\\\|\\&|,")
_RANGE_SEPARATORS_RE = re.compile(r"--|\u2013|-")

_DEMI_JOURNEE_RE = re.compile(r"\bdemi-(jour)", re.IGNORECASE)
_JOUR_RE = re.compile(r"\bjour|\bquotidien", re.IGNORECASE)
# UN/ECE Rec 20 codes, as BT-130 requires (BR-CL-23). Rec 20 has no
# half-day: half-day lines become half a DAY at the day price.
_DAY_UNIT = "DAY"
_LUMP_SUM_UNIT = "LS"


class DocumentMigrationError(InvoicezError):
    pass


def is_document(data: Any, *, file_name: str) -> bool:
    return (
        isinstance(data, dict)
        and ("products" in data or "cancels" in data)
        and (
            "document_number" in data
            or "invoice_number" in data
            or _DOCUMENT_NUMBER_RE.search(file_name) is not None
        )
    )


def parse_legacy_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if match := _SLASH_DATE_RE.match(text):
        day, month, year = map(int, match.groups())
        return date(year, month, day)
    if match := _FRENCH_DATE_RE.match(text):
        day, month_name, year = match.groups()
        if month_name.lower() in _FRENCH_MONTHS:
            return date(int(year), _FRENCH_MONTHS[month_name.lower()], int(day))
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    msg = f"unrecognised date {value!r}"
    raise ValueError(msg)


def parse_legacy_days(text: str) -> list[date]:
    """Expand a legacy line date into the sorted list of days it covers.

    Day and month may be omitted on the left of a range or of a list item, in
    which case they are taken from the next date on the right: in
    "10,16,17,20/3/2023", 10 is 10/3/2023; in "30/11--2/12/2022", 30/11 is
    30/11/2022.

    Returns:
        The days covered, sorted and without duplicates.

    Raises:
        ValueError: If the text is not a list of days and ranges.
    """
    items = [item.strip() for item in _ITEM_SEPARATORS_RE.split(text)]
    if not all(items):
        msg = f"unrecognised line date {text!r}"
        raise ValueError(msg)

    days: set[date] = set()
    month: int | None = None
    year: int | None = None
    for item in reversed(items):
        bounds: list[date] = []
        for part in reversed(_RANGE_SEPARATORS_RE.split(item)):
            try:
                numbers = [int(number) for number in part.strip().split("/")]
            except ValueError:
                msg = f"unrecognised line date {text!r}"
                raise ValueError(msg) from None
            if len(numbers) > 1:
                month = numbers[1]
            if len(numbers) > 2:
                year = numbers[2]
            if len(numbers) > 3 or month is None or year is None:
                msg = f"unrecognised line date {text!r}"
                raise ValueError(msg)
            bounds.insert(0, date(year, month, numbers[0]))
        if len(bounds) > 2 or bounds[0] > bounds[-1]:
            msg = f"unrecognised line date {text!r}"
            raise ValueError(msg)
        days.update(
            bounds[0] + timedelta(days=offset)
            for offset in range((bounds[-1] - bounds[0]).days + 1)
        )
    return sorted(days)


def contiguous_spans(days: list[date]) -> list[tuple[date, date]]:
    spans: list[tuple[date, date]] = []
    for day in days:
        if spans and day - spans[-1][1] == timedelta(days=1):
            spans[-1] = (spans[-1][0], day)
        else:
            spans.append((day, day))
    return spans


def infer_unit(description: str) -> str:
    if _DEMI_JOURNEE_RE.search(description) or _JOUR_RE.search(description):
        return _DAY_UNIT
    return _LUMP_SUM_UNIT


def _is_priced_per_day(description: str) -> bool:
    return infer_unit(description) == _DAY_UNIT


def _as_day_label(match: re.Match[str]) -> str:
    # "Demi-journée" -> "Journée", "demi-journée" -> "journée".
    day = match[1]
    return day[0].upper() + day[1:] if match[0][0].isupper() else day


def _as_int_if_whole(number: float) -> float:
    return int(number) if float(number).is_integer() else number


def _migrate_product(
    product: dict[str, Any], index: int, warnings: list[str], errors: list[str]
) -> list[dict[str, Any]]:
    path = f"products[{index}]"
    rest = {k: v for k, v in product.items() if k != "date"}
    if "unit" not in rest:
        description = str(rest.get("description", ""))
        rest["unit"] = infer_unit(description)
        warnings.append(f"{path}.unit: inferred {rest['unit']!r}")
        if _DEMI_JOURNEE_RE.search(description):
            rest["description"] = _DEMI_JOURNEE_RE.sub(_as_day_label, description)
            rest["n"] = _as_int_if_whole(rest["n"] / 2)
            rest["pu"] = _as_int_if_whole(rest["pu"] * 2)
            warnings.append(
                f"{path}: half-days as {rest['description']!r}, n={rest['n']} DAY "
                f"at pu={rest['pu']}"
            )
    if "date" not in product:
        return [rest]

    try:
        days = parse_legacy_days(str(product["date"]))
    except ValueError as e:
        errors.append(f"{path}.date: {e}")
        return [product]

    spans = contiguous_spans(days)
    if len(spans) == 1:
        return [_with_span(spans[0], rest)]

    if not _is_priced_per_day(str(rest.get("description", ""))):
        errors.append(
            f"{path}: {product['date']!r} covers {len(spans)} spans but the "
            "line is not priced per day, so it cannot be split by days"
        )
        return [product]
    n_per_day = rest["n"] / len(days)
    lines = []
    for span in spans:
        n = n_per_day * ((span[1] - span[0]).days + 1)
        if not (2 * n).is_integer():
            errors.append(
                f"{path}: cannot split n={rest['n']} over {product['date']!r} by days"
            )
            return [product]
        lines.append(_with_span(span, {**rest, "n": _as_int_if_whole(n)}))
    split_n = ", ".join(str(line["n"]) for line in lines)
    warnings.append(
        f"{path}: split {product['date']!r} (n={rest['n']}) into one line per "
        f"span (n={split_n})"
    )
    return lines


def _with_span(span: tuple[date, date], line: dict[str, Any]) -> dict[str, Any]:
    start, end = span
    return {"start": start, **({"end": end} if end != start else {}), **line}


def _company_from_file_name(file_name: str, companies: Mapping[str, str]) -> str | None:
    match = _DOCUMENT_NUMBER_RE.search(file_name)
    if match is None:
        return None
    rest = file_name[match.end() :].removeprefix("-")
    for name, key in companies.items():
        if rest == name or rest.startswith(f"{name}-") or rest == f"{name}.yml":
            return key
    return None


def _rename_keys(data: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    new: dict[str, Any] = {}
    for key, value in data.items():
        new_key = _RENAMES.get(key, key)
        if new_key != key and new_key in data:
            errors.append(f"{key}: both {key!r} and {new_key!r} are set")
            continue
        new[new_key] = value
    return new


def _fill_document_number(
    new: dict[str, Any], file_name: str, warnings: list[str], errors: list[str]
) -> dict[str, Any]:
    if "document_number" in new:
        return new
    match = _DOCUMENT_NUMBER_RE.search(file_name)
    if match is None:
        errors.append("document_number: missing, and not found in the file name")
        return new
    warnings.append(f"document_number: {match.group()!r} from the file name")
    return {"document_number": match.group(), **new}


def _reduce_credit_note(new: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    dropped = [key for key in new if key not in _CREDIT_NOTE_KEYS]
    if not dropped:
        return new
    warnings.append(
        f"credit note: dropped {', '.join(dropped)}; the content now comes from "
        f"the cancelled invoice {new['cancels']}"
    )
    return {key: new[key] for key in _CREDIT_NOTE_KEYS if key in new}


def _migrate_invoice(
    new: dict[str, Any],
    file_name: str,
    companies: Mapping[str, str],
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any]:
    if "company" not in new:
        company = _company_from_file_name(file_name, companies)
        if company is None:
            warnings.append("company: missing, and not found in the file name")
        else:
            warnings.append(f"company: {company!r} from the file name")
            new = {"company": company, **new}
    new["products"] = [
        line
        for index, product in enumerate(new.get("products", []))
        for line in _migrate_product(product, index, warnings, errors)
    ]
    return new


def migrate_document_mapping(
    data: Mapping[str, Any], *, file_name: str, companies: Mapping[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """Transform a legacy document mapping into the current document schema.

    Renames legacy keys, turns legacy dates into real dates and legacy line
    dates into start/end (one line per contiguous span), infers missing line
    units, fills the document number and company from the file name when
    missing and reduces legacy credit notes to what resolve_cancellation needs.
    Idempotent.

    Args:
        data: The document mapping, as loaded from YAML.
        file_name: The document's file name, e.g.
            "Facture-2020-10-001-Orsys-BDAZZ1.yml".
        companies: Company display name to company key, from variables.yml.

    Returns:
        The migrated mapping, plus warnings about what was inferred.

    Raises:
        DocumentMigrationError: If the document cannot be migrated safely.
    """
    warnings: list[str] = []
    errors: list[str] = []

    new = _rename_keys(data, errors)
    new = _fill_document_number(new, file_name, warnings, errors)
    for key in ("date", "payment_due_date"):
        if key in new:
            try:
                new[key] = parse_legacy_date(new[key])
            except ValueError as e:
                errors.append(f"{key}: {e}")
    if "cancels" in new:
        new = _reduce_credit_note(new, warnings)
    else:
        new = _migrate_invoice(new, file_name, companies, warnings, errors)

    warnings.extend(
        f"{key}: not part of the document schema"
        for key in new
        if key not in _ALLOWED_KEYS
    )

    if errors:
        raise DocumentMigrationError("; ".join(errors))
    return new, warnings
