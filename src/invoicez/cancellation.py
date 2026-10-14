from pathlib import Path
from typing import Any

from yaml import safe_load as yaml_safe_load

from invoicez.numbering import find_document_path


def resolve_cancellation(data: dict[str, Any], git_dir: Path) -> dict[str, Any]:
    if "cancels" not in data:
        return data

    resolved_path = find_document_path(git_dir, data["cancels"])
    resolved = yaml_safe_load(resolved_path.read_text(encoding="utf8"))

    resolved["previous_document_number"] = data["cancels"]
    resolved["previous_document_date"] = resolved["date"]
    # The credit note's due date derives from its own date, not the invoice's.
    resolved.pop("payment_due_date", None)
    if "description" in resolved:
        resolved["description"] = f"Facture d'avoir pour « {resolved['description']} »"  # ruff: ignore[ambiguous-unicode-character-string]
    resolved["products"] = [
        {**product, "description": f"Avoir pour « {product['description']} »"}  # ruff: ignore[ambiguous-unicode-character-string]
        for product in resolved["products"]
    ]
    del data["cancels"]

    resolved["date"] = data["date"]
    resolved["document_number"] = data["document_number"]
    resolved.setdefault("invoice_meta", {})["type_code"] = "381"
    return resolved
