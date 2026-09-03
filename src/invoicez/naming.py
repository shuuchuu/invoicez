from collections.abc import Mapping
from pathlib import Path
from typing import Any

from invoicez.variables import Variables


def name_document(
    name: str,
    document_number: str,
    document_data: Mapping[str, Any],
    global_data: Mapping[str, Any],
    template_invoice: str,
    template_quote: str,
    quote: bool,
    git_dir: Path,
) -> str:
    variables = Variables.from_mappings(global_data, document_data)

    template = template_quote if quote else template_invoice
    return (
        template.replace("{name}", name)
        .replace("{company}", variables.company.name)
        .replace("{document_number}", document_number)
    )
