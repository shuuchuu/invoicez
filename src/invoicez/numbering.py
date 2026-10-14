import re
from datetime import date
from pathlib import Path

from yaml import safe_load as yaml_safe_load

from invoicez.exceptions import InvoicezError

_DOCUMENT_NUMBER_RE = re.compile(r"(?P<prefix>\d{4}-\d{2})-(?P<number>\d{3})")


def is_document_number(text: str) -> bool:
    return _DOCUMENT_NUMBER_RE.fullmatch(text) is not None


def find_document_path(
    git_dir: Path, document_number: str, *, quote: bool = False
) -> Path:
    """Return the path of the document with the given number.

    Args:
        git_dir: Directory searched recursively for documents.
        document_number: Number of the document, e.g. "2026-10-004".
        quote: Look for a quote rather than an invoice, as they are numbered
            separately.

    Returns:
        Path of the document.

    Raises:
        InvoicezError: If no document has this number.
    """
    for item in git_dir.glob("**/*.yml"):
        content = yaml_safe_load(item.read_text(encoding="utf8"))
        if (
            isinstance(content, dict)
            and str(content.get("document_number")) == document_number
            and bool(content.get("quote")) == quote
        ):
            return item
    kind = "quote" if quote else "invoice"
    msg = f"Could not find an {kind} numbered {document_number!r}."
    raise InvoicezError(msg)


def get_next_document_number(
    git_dir: Path, *, quote: bool = False, today: date | None = None
) -> str:
    """Return the number following the highest one of the month.

    Invoices (credit notes included) and quotes are numbered separately.

    Args:
        git_dir: Directory searched recursively for documents.
        quote: Number a quote rather than an invoice.
        today: The date whose month to number in. Defaults to today.

    Returns:
        The next document number, e.g. "2026-10-004".

    Raises:
        InvoicezError: If two documents of the month share a number.
    """
    prefix = (today or date.today()).strftime("%Y-%m")
    paths_by_number: dict[int, Path] = {}
    for path in sorted(git_dir.glob("**/*.yml")):
        content = yaml_safe_load(path.read_text(encoding="utf8"))
        if not isinstance(content, dict) or bool(content.get("quote")) != quote:
            continue
        match = _DOCUMENT_NUMBER_RE.fullmatch(str(content.get("document_number")))
        if match is None or match["prefix"] != prefix:
            continue
        number = int(match["number"])
        if number in paths_by_number:
            msg = (
                f"{paths_by_number[number]} and {path} share the document number "
                f"{prefix}-{number:03}."
            )
            raise InvoicezError(msg)
        paths_by_number[number] = path
    return f"{prefix}-{max(paths_by_number, default=0) + 1:03}"
