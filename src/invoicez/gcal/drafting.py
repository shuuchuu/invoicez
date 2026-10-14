"""Draft invoice documents from priced engagements."""

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from invoicez.exceptions import InvoicezError
from invoicez.gcal.grouping import Engagement
from invoicez.gcal.pricing import Pricing, number, render_template
from invoicez.gcal.settings import GcalSettings
from invoicez.naming import name_document
from invoicez.settings import Settings
from invoicez.variables import InvoiceLine, Variables

# Characters LaTeX reads as commands unless escaped. Documents hold LaTeX (e.g.
# \textit{...}, \&) and the PDF templates do not escape them.
_LATEX_SPECIAL_RE = re.compile(r"(?<!\\)[&%$#_]")


def _check_latex(document: Mapping[str, Any]) -> None:
    texts = {
        "description": document["description"],
        "reference": document.get("reference") or "",
        **{
            f"line {i}": product["description"]
            for i, product in enumerate(document["products"], start=1)
        },
    }
    problems = [
        f"{field} {text!r} has an unescaped {match[0]!r} (write \\{match[0]})"
        for field, text in texts.items()
        if (match := _LATEX_SPECIAL_RE.search(text))
    ]
    if problems:
        msg = f"the PDF would not build: {'; '.join(problems)}"
        raise InvoicezError(msg)


def _plain(value: float) -> int | float:
    # 2 rather than 2.0 in the YAML, as in hand-written documents.
    return int(value) if float(value).is_integer() else value


def _product(line: InvoiceLine) -> dict[str, Any]:
    product = line.model_dump(exclude_none=True)
    product["n"] = _plain(line.n)
    product["pu"] = _plain(line.pu)
    return product


def draft(
    engagement: Engagement,
    pricing: Pricing,
    *,
    document_number: str,
    today: date,
    reference: str | None,
    settings: GcalSettings,
    global_mapping: Mapping[str, Any],
    calendar_id: str,
) -> dict[str, Any]:
    """Build the YAML mapping of an invoice for an engagement.

    Args:
        engagement: The engagement to bill.
        pricing: Its pricing, from price.
        document_number: The invoice number, from get_next_document_number.
        today: The invoice date.
        reference: The client's reference (BT-12 or BT-13), usually the
            engagement description's ref.
        settings: The gcal settings, for the description template.
        global_mapping: The content of variables.yml.
        calendar_id: The calendar the engagement comes from.

    Returns:
        The document mapping, checked to build valid Variables.

    Raises:
        InvoicezError: If the document would not be valid, or its texts hold
            characters that LaTeX needs escaped.
    """
    document: dict[str, Any] = {
        "document_number": document_number,
        "date": today.isoformat(),
        "company": pricing.company,
        "description": render_template(
            settings.templates.description,
            company=pricing.company,
            training=pricing.training,
            days=number(pricing.days),
            start=engagement.start,
            end=engagement.end,
            reference=reference,
        ),
        "products": [_product(line) for line in pricing.lines],
        "source": {
            "kind": "gcal",
            "calendar_id": calendar_id,
            "event_ids": [session.event_id for session in engagement.sessions],
            "fingerprint": engagement.fingerprint,
        },
    }
    if reference is not None:
        document["reference"] = reference
    _check_latex(document)
    if pricing.company not in global_mapping.get("companies", {}):
        msg = f"company {pricing.company!r} missing from variables.yml"
        raise InvoicezError(msg)
    try:
        Variables.from_mappings(global_mapping, document)
    except ValidationError as e:
        msg = f"invalid draft for {engagement.root.title!r}:\n{e}"
        raise InvoicezError(msg) from e
    return document


def draft_path(
    document: Mapping[str, Any],
    *,
    name: str,
    settings: Settings,
    global_mapping: Mapping[str, Any],
    git_dir: Path,
) -> Path:
    """Return where to write a draft, relative to the data repository.

    Returns:
        The path, e.g. gcal-ymls/2026/Facture-2026-10-004-Orsys-BDAZZ1.yml.

    Raises:
        InvoicezError: If settings.yml has no gcal section.
    """
    if settings.gcal is None:
        msg = "settings.yml has no gcal section"
        raise InvoicezError(msg)
    year = date.fromisoformat(document["date"]).year
    file_name = name_document(
        name,
        document["document_number"],
        document,
        global_mapping,
        settings.invoice_template,
        settings.quote_template,
        False,
        git_dir,
    )
    return Path(settings.gcal.output_dir.format(year=year)) / f"{file_name}.yml"
