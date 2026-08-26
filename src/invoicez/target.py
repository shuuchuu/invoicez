from logging import getLogger
from pathlib import Path

from yaml import safe_load as yaml_safe_load

from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths

_logger = getLogger(__name__)


class Target:
    def __init__(self, path: Path, template_name: str, paths: Paths):
        if not path.is_file():
            msg = f"Could not find the file to compile {path}."
            raise InvoicezError(msg)
        with path.open(encoding="utf8") as fh:
            self.data = yaml_safe_load(fh)
        self.name = path.with_suffix("").name
        self.path = path
        self.template_name = template_name
        full_template_path = paths.jinja2_dir / template_name
        if not full_template_path.name.endswith(".tex"):
            full_template_path = full_template_path.with_suffix(".tex")
        if not full_template_path.is_file():
            msg = f"Could not find the template to use {full_template_path}."
            raise InvoicezError(msg)
        self.template_path = full_template_path.name
