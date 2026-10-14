from logging import getLogger
from pathlib import Path

from yaml import safe_dump, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.migration.documents import (
    DocumentMigrationError,
    is_document,
    migrate_document_mapping,
)
from invoicez.paths import Paths

_logger = getLogger(__name__)


def migrate_documents(root: Path = Path(), *, write: bool = False) -> None:
    """Migrate legacy document YAMLs to the current document schema.

    Dry run by default: lists what would change in each document. Review the
    result of --write with git diff.

    Args:
        root: Directory searched recursively for document YAMLs.
        write: Rewrite the migrated documents in place.

    Raises:
        InvoicezError: If some documents could not be migrated.
    """
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    companies = {
        company["name"]: key for key, company in global_data["companies"].items()
    }

    n_changed = 0
    failed: list[Path] = []
    for path in sorted(root.glob("**/*.yml")):
        data = safe_load(path.read_text(encoding="utf8"))
        if not is_document(data, file_name=path.name):
            continue
        try:
            new, warnings = migrate_document_mapping(
                data, file_name=path.name, companies=companies
            )
        except DocumentMigrationError as e:
            _logger.error(f"{path}: {e}")
            failed.append(path)
            continue
        if new == data:
            continue
        n_changed += 1
        _logger.info(f"{path}: {'migrated' if write else 'would be migrated'}")
        for warning in warnings:
            _logger.warning(f"{path}: {warning}")
        if write:
            path.write_text(
                safe_dump(new, allow_unicode=True, sort_keys=False), encoding="utf8"
            )

    verb = "Migrated" if write else "Would migrate"
    _logger.info(f"{verb} {n_changed} document(s), {len(failed)} error(s).")
    if failed:
        msg = f"{len(failed)} document(s) could not be migrated, see above."
        raise InvoicezError(msg)
