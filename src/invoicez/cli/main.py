from pathlib import Path

from rich.console import Console

from ..calendar import GoogleCalendar  # type: ignore
from ..cli import app
from ..compiling.billing import Biller
from ..config.logging import setup_logging
from ..config.paths import Paths
from ..config.settings import Settings
from ..directing import Director
from ..scheduling.mapping import Mapper
from ..scheduling.merging import Merger


@app.callback(invoke_without_command=True)
def main() -> None:
    setup_logging()
    paths = Paths(Path("."))
    settings = Settings.load(paths)
    console = Console()

    Director(
        calendar=GoogleCalendar(paths, settings, console),
        merger=Merger(paths, settings),
        mapper=Mapper(paths, settings),
        biller=Biller(paths, settings),
    ).direct()
