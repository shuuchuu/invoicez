from invoicez.paths import Paths
from invoicez.runner import run as runner_run


def run(document: str, template: str = "main", *, quote: bool = False) -> None:
    """Build a document into the sendbox directory.

    Args:
        document: YAML file of the invoice or quote, or its number (e.g. 2026-10-003).
        template: Template to use for the compilation.
        quote: Look the number up among quotes rather than invoices.
    """
    paths = Paths()
    runner_run(
        paths.resolve_document(document, quote=quote),
        template,
        paths.variables,
        paths.jinja2_dir,
        paths.git_dir,
        paths.assets_dir,
        paths.build_dir,
        paths.sendbox_dir,
    )
