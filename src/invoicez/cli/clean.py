from logging import getLogger
from shutil import rmtree

from invoicez.paths import Paths


def clean() -> None:
    """Wipe out the build directory."""
    logger = getLogger(__name__)
    paths = Paths()
    if not paths.build_dir.exists():
        logger.info(f"Nothing to do: {paths.build_dir} doesn't exist")
    else:
        logger.info(f"Deleting {paths.build_dir}")
        rmtree(paths.build_dir)
