from pathlib import Path

from invoicez.cli import ExistingYAMLPath
from invoicez.paths import Paths
from invoicez.runner import run as runner_run


def run(path: ExistingYAMLPath, template: str = "main") -> None:
    """Run the compiler.

    Args:
        path: YAML file of the invoice or quote you want to compile.
        template: Template to use for the compilation.
    """
    paths = Paths()
    runner_run(
        Path(path),
        template,
        paths.variables,
        paths.jinja2_dir,
        paths.git_dir,
        paths.assets_dir,
        paths.build_dir,
        paths.pdf_dir,
    )
