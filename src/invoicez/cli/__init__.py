import sys
from logging import INFO, basicConfig
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter, validators
from rich.logging import RichHandler

from invoicez import __version__

app = App(version=__version__)

app.register_install_completion_command()

# Lazy load commands to keep completion fast
app.command("invoicez.cli.new:new")
app.command("invoicez.cli.cancel:cancel")
app.command("invoicez.cli.run:run")
app.command("invoicez.cli.clean:clean")
app.command("invoicez.cli.send:send")
app.command("invoicez.cli.login:login")


ExistingYAMLPath = Annotated[
    Path,
    Parameter(
        validator=validators.Path(exists=True, dir_okay=False, ext=["yml", "yaml"])
    ),
]


def main(*args: str) -> None:
    basicConfig(
        level=INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, tracebacks_show_locals=False)],
    )
    app(args or sys.argv[1:])
