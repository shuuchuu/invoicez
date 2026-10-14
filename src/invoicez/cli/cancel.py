from datetime import datetime
from logging import getLogger

from yaml import safe_dump, safe_load

from invoicez.numbering import get_next_document_number
from invoicez.paths import Paths

_logger = getLogger(__name__)


def cancel(document: str) -> None:
    """Create a credit note (avoir) referencing an existing one.

    The credit note is written in the current directory.

    Args:
        document: YAML file of the invoice to cancel, or its number (e.g.
            2026-10-003).
    """
    paths = Paths()

    # Load the invoice to cancel, only to get its number and company
    path = paths.resolve_document(document)
    document_data = safe_load(path.read_text(encoding="utf8"))
    original_document_number = document_data["document_number"]

    document_number = get_next_document_number(paths.git_dir)

    output_path = paths.working_dir / path.name.replace(
        original_document_number, document_number
    )

    content = {
        "document_number": document_number,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "cancels": original_document_number,
    }

    _logger.info(
        "Creating new cancelling invoice in "
        f"{output_path.relative_to(paths.working_dir)}"
    )
    with output_path.open("w", encoding="utf8") as fh:
        safe_dump(content, fh, allow_unicode=True)
