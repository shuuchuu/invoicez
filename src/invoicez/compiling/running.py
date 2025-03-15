from pathlib import Path

from ..config.paths import Paths
from ..config.settings import Settings
from .building import Builder
from .target import Target


def run(path: Path, template: str, paths: Paths) -> None:
    settings = Settings.load(paths)
    target = Target(path, template, paths)
    Builder(target, settings, paths)
