from importlib.resources import files
from logging import getLogger
from pathlib import Path

from invoicez.exceptions import InvoicezError

_logger = getLogger(__name__)

_SKILL_NAME = "sync-jinja-template"


def install_skill(*, force: bool = False) -> None:
    """Install the sync-jinja-template Claude Code skill in the current directory.

    Copies the skill bundled with invoicez to ./.claude/skills/, so it can
    be invoked from a Claude Code session rooted in a companion data repo
    (not the invoicez source tree) to audit that repo's Jinja templates.

    Args:
        force: Overwrite the skill file if it already exists.

    Raises:
        InvoicezError: If the target file already exists and force is not set.
    """
    source = files("invoicez.skills").joinpath(_SKILL_NAME, "SKILL.md")
    target = Path(".claude") / "skills" / _SKILL_NAME / "SKILL.md"

    if target.exists() and not force:
        msg = f"{target} already exists. Pass --force to overwrite it."
        raise InvoicezError(msg)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf8"), encoding="utf8")
    _logger.info(f"Installed the {_SKILL_NAME} skill to {target}")
