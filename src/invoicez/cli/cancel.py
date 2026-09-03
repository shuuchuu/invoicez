from datetime import datetime
from logging import getLogger

from yaml import safe_dump, safe_load

from invoicez.cli import ExistingYAMLPath
from invoicez.numbering import get_next_document_number
from invoicez.paths import Paths

_logger = getLogger(__name__)


def cancel(path: ExistingYAMLPath) -> None:
    """Create a credit note (avoir) referencing an existing one.

    Args:
        path: Path of the invoice for which to create a credit note.
    """
    paths = Paths()

    # Load the invoice to cancel, only to get its number and company
    path = path if path.is_absolute() else paths.working_dir / path
    document_data = safe_load((paths.working_dir / path).read_text(encoding="utf8"))
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
