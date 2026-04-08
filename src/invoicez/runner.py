from pathlib import Path

from invoicez.builder import Builder
from invoicez.config import get_config
from invoicez.paths import Paths
from invoicez.target import Target


def run(path: Path, template: str, paths: Paths) -> None:
    config = get_config(paths)
    target = Target(path, template, paths)
    Builder(target, config, paths)
