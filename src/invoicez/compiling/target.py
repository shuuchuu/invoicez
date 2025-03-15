from pathlib import Path

from yaml import safe_load as yaml_safe_load

from ..config.paths import Paths
from ..exceptions import InvoicezError


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
        self.template_path = paths.templates_dir / template_name
        if not self.template_path.name.endswith(".tex"):
            self.template_path = self.template_path.with_suffix(".tex")
        if not self.template_path.is_file():
            msg = f"Could not find the template to use {self.template_path}."
            raise InvoicezError(msg)
