from pathlib import Path
from typing import Any

from invoicez.cli import command, dir_path_option, option, path_argument
from invoicez.paths import Paths
from invoicez.runner import run as runner_run


def _autocomplete_template(ctx: Any, args: list[str], incomplete: str) -> list[str]:
    try:
        paths = Paths(Path())
        return [
            t.with_suffix("").with_suffix("").name
            for t in paths.jinja2_dir.glob("*.tex.jinja2")
        ]
    except Exception:
        return []


@command
@path_argument
@option(
    "--template",
    type=str,
    default="main",
    shell_complete=_autocomplete_template,
)
@dir_path_option
def run(path: str, template: str, dir_path: str) -> None:
    """Run the compiler."""
    paths = Paths(Path(dir_path))
    runner_run(Path(path), template, paths)
