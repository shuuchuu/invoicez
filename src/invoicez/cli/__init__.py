import sys
from logging import INFO, basicConfig
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter, validators
from rich.logging import RichHandler

from invoicez import __version__


class _App(App):
    def generate_completion(
        self,
        *,
        prog_name: str | None = None,
        shell: Literal["zsh", "bash", "fish"] | None = None,
    ) -> str:
        script = super().generate_completion(prog_name=prog_name, shell=shell)
        if not script.startswith("#compdef"):
            return script
        # Imported here to keep startup fast.
        from invoicez.cli.completion import add_document_completion

        return add_document_completion(script, prog_name or self.name[0])


app = _App(version=__version__)

app.register_install_completion_command()

# Lazy load commands to keep completion fast
app.command("invoicez.cli.new:new")
app.command("invoicez.cli.cancel:cancel")
app.command("invoicez.cli.run:run")
app.command("invoicez.cli.clean:clean")
app.command("invoicez.cli.send:send")
app.command("invoicez.cli.status:status")
app.command("invoicez.cli.ledger:ledger")
app.command("invoicez.cli.login:login")
app.command("invoicez.cli.migrate:migrate_config")
app.command("invoicez.cli.migrate_documents:migrate_documents")
app.command("invoicez.cli.install_skill:install_skill")
app.command("invoicez.cli.completion:complete_documents", show=False)
app.command(
    "invoicez.cli.gcal:app", name="gcal", help="Bill trainings from a Google Calendar."
)


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
