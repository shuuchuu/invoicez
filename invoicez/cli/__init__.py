from functools import partial, wraps
from importlib import import_module
from importlib import invalidate_caches as importlib_invalidate_caches
from logging import INFO
from pathlib import Path
from pkgutil import walk_packages
from typing import Any, Callable, List

from click import ClickException
from click import Path as ClickPath
from click import argument, group
from click import option as click_option
from coloredlogs import install as coloredlogs_install

from invoicez.exceptions import InvoicezException
from invoicez.paths import Paths

option = partial(click_option, show_default=True)

dir_path_option = option(
    "--dir-path",
    type=ClickPath(exists=True, readable=True, file_okay=False),
    default=".",
    help="Path of the working directory.",
)

quote_option = option(
    "--quote",
    is_flag=True,
    help="If set, resulting yaml will be a quote not, an invoice.",
)


def _autocomplete_path(ctx: Any, args: List[str], incomplete: str) -> List[str]:
    try:
        paths = Paths(Path("."))
        return [
            str(t.relative_to(paths.working_dir))
            for t in paths.working_dir.glob("*.yml")
            if t.name != "company-config.yml"
        ]
    except Exception:
        return []


path_argument = argument(
    "path",
    type=ClickPath(exists=True, dir_okay=False, readable=True),
    shell_complete=_autocomplete_path,
)


@group(chain=True)
def cli() -> None:
    coloredlogs_install(
        level=INFO,
        fmt="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


def command(f: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = f(*args, **kwargs)
        except InvoicezException as e:
            raise ClickException(str(e)) from e
        return result

    return cli.command()(wrapper)


def _import_module_and_submodules(package_name: str) -> None:
    """
    From https://github.com/allenai/allennlp/blob/master/allennlp/common/util.py
    """
    importlib_invalidate_caches()

    module = import_module(package_name)
    path = getattr(module, "__path__", [])
    path_string = "" if not path else path[0]

    for module_finder, name, _ in walk_packages(path):
        if path_string and module_finder.path != path_string:  # type: ignore
            continue
        subpackage = f"{package_name}.{name}"
        _import_module_and_submodules(subpackage)


_import_module_and_submodules(__name__)
