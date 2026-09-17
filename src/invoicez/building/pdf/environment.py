from datetime import date
from os.path import join as path_join
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from invoicez.exceptions import InvoicezError


def _to_camel_case(string: str) -> str:
    return "".join(substring.capitalize() or "_" for substring in string.split("_"))


def _to_int_or_float(number: int | float) -> str:
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _display_date_range(start: date | None, end: date | None = None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return f"{start.day}/{start.month}/{start.year}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}-{end.day}/{start.month}/{start.year}"
    return f"{start.day}/{start.month}/{start.year}-{end.day}/{end.month}/{end.year}"


def create_environment(jinja2_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(searchpath=jinja2_dir),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\V{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        autoescape=False,
    )
    environment.filters["camelcase"] = _to_camel_case
    environment.filters["path_join"] = lambda paths: path_join(*paths)  # ruff: ignore[os-path-join]
    environment.filters["display_number"] = _to_int_or_float
    environment.filters["display_date_range"] = _display_date_range
    return environment


def resolve_template(template: str, jinja2_dir: Path) -> str:
    full_template_path = jinja2_dir / template
    if not full_template_path.name.endswith(".tex"):
        full_template_path = full_template_path.with_suffix(".tex")
    if not full_template_path.is_file():
        msg = f"Could not find the template to use {full_template_path}."
        raise InvoicezError(msg)
    return full_template_path.name
