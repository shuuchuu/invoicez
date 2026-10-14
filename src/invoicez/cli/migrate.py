from logging import getLogger
from pathlib import Path

from yaml import SafeDumper, ScalarNode, dump, safe_dump, safe_load

from invoicez.cli import ExistingYAMLPath
from invoicez.exceptions import InvoicezError
from invoicez.migration.config import migrate_config_mapping, migrate_gcal_settings

_logger = getLogger(__name__)


class _BlockDumper(SafeDumper):
    """Dump multi-line strings as | blocks, e.g. the verbose title_pattern."""


def _represent_str(dumper: SafeDumper, value: str) -> ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockDumper.add_representer(str, _represent_str)


def migrate_config(
    old_config: ExistingYAMLPath,
    output: Path = Path("variables.yml"),
    *,
    force: bool = False,
    gcal: bool = False,
) -> None:
    """Migrate an old-format config.yml to the current variables.yml schema.

    Args:
        old_config: Path of the old config.yml to migrate.
        output: Where to write the migrated variables.yml.
        force: Overwrite output if it already exists.
        gcal: Instead, print the gcal section to paste into settings.yml.

    Raises:
        InvoicezError: If output already exists and force is not set.
    """
    if gcal:
        _print_gcal_settings(old_config)
        return

    if output.exists() and not force:
        msg = f"{output} already exists. Pass --force to overwrite it."
        raise InvoicezError(msg)

    old = safe_load(old_config.read_text(encoding="utf8"))
    new, warnings = migrate_config_mapping(old)

    header = "\n".join(f"# TODO: {warning}" for warning in warnings)
    content = safe_dump(new, allow_unicode=True, sort_keys=True)
    full_content = f"{header}\n\n{content}" if warnings else content
    output.write_text(full_content, encoding="utf8")

    _logger.info(f"Wrote migrated config to {output}")
    for warning in warnings:
        _logger.warning(warning)
    if warnings:
        _logger.warning(f"{len(warnings)} item(s) need manual review, see above.")


def _print_gcal_settings(old_config: Path) -> None:
    # Warnings go in the output as comments: logs would end up in stdout too.
    old = safe_load(old_config.read_text(encoding="utf8"))
    section, warnings = migrate_gcal_settings(old)

    header = "".join(f"# TODO: {warning}\n" for warning in warnings)
    content = dump(
        {"gcal": section}, Dumper=_BlockDumper, allow_unicode=True, sort_keys=False
    )
    print(f"{header}{content}", end="")
