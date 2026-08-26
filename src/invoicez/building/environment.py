from os.path import join as path_join
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def _to_camel_case(string: str) -> str:
    return "".join(substring.capitalize() or "_" for substring in string.split("_"))


def _to_int_or_float(number: int | float) -> str:
    return f"{number:.2f}".rstrip("0").rstrip(".")


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
    return environment
