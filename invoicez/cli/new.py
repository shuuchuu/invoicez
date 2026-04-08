from datetime import datetime
from logging import getLogger
from pathlib import Path

from click import argument
from yaml import safe_dump as yaml_safe_dump
from yaml import safe_load as yaml_safe_load

from invoicez.cli import command, dir_path_option, path_argument, quote_option
from invoicez.config import get_config
from invoicez.paths import Paths

_logger = getLogger(__name__)


@command
@path_argument
@argument("name")
@dir_path_option
@quote_option
def new(path: str, name: str, dir_path: str, quote: bool) -> None:
    """Create a new invoice based on an existing one."""
    paths = Paths(Path(dir_path))
    config = get_config(paths)

    # Load invoice on which to base the new one
    content = yaml_safe_load((paths.working_dir / path).read_text(encoding="utf8"))

    doc_type = "quote" if quote else "invoice"
    template_type = "quote_template" if quote else "name_template"
    doc_number = _get_next_doc_number(paths, quote)
    output_name = (
        config[f"{template_type}"]
        .replace("{name}", name)
        .replace("{company}", config["companies"][content["company"]]["name"])
        .replace(f"{{{doc_type}_number}}", doc_number)
    )
    output_path = (paths.working_dir / output_name).with_suffix(".yml")

    now = datetime.now()
    date = now.strftime("%d/%m/%Y")

    content["date"] = date
    content[f"{doc_type}_number"] = doc_number
    _logger.info(
        f"Creating new {doc_type} in {output_path.relative_to(paths.working_dir)}"
    )
    with output_path.open("w", encoding="utf8") as fh:
        yaml_safe_dump(content, fh, allow_unicode=True)


def _get_next_doc_number(paths: Paths, quote_option: bool) -> str:
    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    n_documents = 0
    is_doc = _is_quote if quote_option else _is_invoice
    for item in paths.git_dir.glob("**/*.yml"):
        if is_doc(item):
            n_documents += 1
    return f"{prefix}-{n_documents + 1:03}"


def _is_invoice(path: Path) -> bool:
    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    content = yaml_safe_load(path.read_text(encoding="utf8"))
    return "invoice_number" in content and content["invoice_number"].startswith(prefix)


def _is_quote(path: Path) -> bool:
    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    content = yaml_safe_load(path.read_text(encoding="utf8"))
    return (
        not _is_invoice(path)
        and "quote_number" in content
        and content["quote_number"].startswith(prefix)
    )
