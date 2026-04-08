from logging import getLogger
from pathlib import Path
from shutil import rmtree

from invoicez.cli import command, dir_path_option
from invoicez.paths import Paths


@command
@dir_path_option
def clean(dir_path: str) -> None:
    """Wipe out the build directory."""
    logger = getLogger(__name__)
    paths = Paths(Path(dir_path))
    if not paths.build_dir.exists():
        logger.info(f"Nothing to do: {paths.build_dir} doesn't exist")
    else:
        logger.info(f"Deleting {paths.build_dir}")
        rmtree(paths.build_dir)
