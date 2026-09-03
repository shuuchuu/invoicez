from datetime import datetime
from logging import getLogger

from yaml import safe_dump, safe_load

from invoicez.naming import name_document
from invoicez.numbering import get_next_document_number
from invoicez.paths import Paths
from invoicez.settings import Settings


def new(path: str, name: str, quote: bool = False) -> None:
    """Create a new invoice based on an existing one.

    Args:
        path: Path of the invoice to use as a base.
        name: How to name the new invoice.
        quote: If set, resulting yaml will be a quote, not an invoice.
    """
    logger = getLogger(__name__)
    paths = Paths()
    settings = Settings.from_path(paths.settings)

    # Load invoice/quote on which to base the new one
    document_data = safe_load((paths.working_dir / path).read_text(encoding="utf8"))
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))

    document_number = get_next_document_number(paths.git_dir, quote=quote)
    document_name = name_document(
        name,
        document_number,
        document_data,
        global_data,
        settings.invoice_template,
        settings.quote_template,
        quote,
        paths.git_dir,
    )

    output_path = (paths.working_dir / document_name).with_suffix(".yml")

    now = datetime.now()
    date = now.strftime("%Y-%m-%d")

    document_data["date"] = date
    document_data["document_number"] = document_number
    if quote:
        document_data["quote"] = True
    else:
        if "quote" in document_data:
            del document_data["quote"]
    logger.info(
        f"Creating new {'quote' if quote else 'invoice'} in "
        f"{output_path.relative_to(paths.working_dir)}"
    )
    with output_path.open("w", encoding="utf8") as fh:
        safe_dump(document_data, fh, allow_unicode=True)
