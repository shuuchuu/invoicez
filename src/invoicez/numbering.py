from datetime import datetime
from pathlib import Path

from yaml import safe_load as yaml_safe_load

from invoicez.exceptions import InvoicezError


def find_document_path(git_dir: Path, document_number: str) -> Path:
    for item in git_dir.glob("**/*.yml"):
        content = yaml_safe_load(item.read_text(encoding="utf8"))
        if content.get("document_number") == document_number:
            return item
    msg = f"Could not find a document numbered {document_number!r}."
    raise InvoicezError(msg)


def is_invoice(path: Path) -> bool:
    prefix = datetime.now().strftime("%Y-%m")
    content = yaml_safe_load(path.read_text(encoding="utf8"))
    return (
        "document_number" in content
        and content["document_number"].startswith(prefix)
        and not content.get("quote", False)
    )


def is_quote(path: Path) -> bool:
    prefix = datetime.now().strftime("%Y-%m")
    content = yaml_safe_load(path.read_text(encoding="utf8"))
    return (
        "document_number" in content
        and content["document_number"].startswith(prefix)
        and content.get("quote", False)
    )


def get_next_document_number(git_dir: Path, *, quote: bool = False) -> str:
    prefix = datetime.now().strftime("%Y-%m")
    is_doc = is_quote if quote else is_invoice
    n_documents = sum(1 for item in git_dir.glob("**/*.yml") if is_doc(item))
    return f"{prefix}-{n_documents + 1:03}"
