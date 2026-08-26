from copy import deepcopy
from logging import getLogger
from multiprocessing import Pool
from os.path import join as path_join
from pathlib import Path
from shutil import copyfile
from subprocess import CompletedProcess, run
from typing import Any

from jinja2 import Environment, FileSystemLoader

from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.target import Target


class Builder:
    def __init__(self, target: Target, config: dict[str, Any], paths: Paths):
        self._target = target
        self._config = config
        self._paths = paths
        self._logger = getLogger(__name__)
        self._track_build()

    def _track_build(self) -> None:
        to_compile = [self._target]
        with Pool(1) as pool:
            completed_processes = pool.map(self._build, to_compile)
        for completed_process, target in zip(
            completed_processes, to_compile, strict=True
        ):
            compilation = f"{target.name}/{target.template_name}"
            if completed_process.returncode != 0:
                self._logger.warning("Compilation %s errored", compilation)
                self._logger.warning(
                    "Captured %s stderr\n%s", compilation, completed_process.stderr
                )
                self._logger.warning(
                    "Captured %s stdout\n%s", compilation, completed_process.stdout
                )

    def _get_filename(self) -> str:
        return self._target.name

    def _build(
        self, target: Target | None, copy_result: bool = True
    ) -> CompletedProcess:
        build_dir = self._setup_build_dir()
        filename = self._get_filename()
        latex_path = build_dir / f"{filename}.tex"
        build_pdf_path = latex_path.with_suffix(".pdf")
        output_pdf_path = self._paths.pdf_dir / f"{filename}.pdf"

        self._write_latex(latex_path)

        completed_process = self._compile(
            latex_path=latex_path.relative_to(build_dir),
            build_dir=build_dir,
        )
        if copy_result and completed_process.returncode == 0:
            self._paths.pdf_dir.mkdir(parents=True, exist_ok=True)
            copyfile(build_pdf_path, output_pdf_path)
        return completed_process

    def _setup_build_dir(self) -> Path:
        target_build_dir = (
            self._paths.build_dir / self._target.name / self._target.template_name
        )
        target_build_dir.mkdir(parents=True, exist_ok=True)
        if self._paths.assets_dir.is_dir():
            for item in self._paths.assets_dir.iterdir():
                self._setup_link(target_build_dir / item.name, item)
        return target_build_dir

    def _write_latex(self, output_path: Path) -> None:
        template = self._env.get_template(str(self._target.template_path.name))
        context = deepcopy(self._target.data)
        for k, v in ((k, v) for k, v in self._config.items() if k != "companies"):
            context[k] = v
        context["company"] = {
            **self._config["company"],
            **self._config["companies"][self._target.data["company"]],
        }
        content = f"{template.render(**context)}\n"
        if (
            not output_path.exists()
            or output_path.read_text(encoding="utf8") != content
        ):
            output_path.write_text(content, encoding="utf8")

    @property
    def _env(self) -> Environment:
        if not hasattr(self, "__env"):
            self.__env = Environment(
                loader=FileSystemLoader(searchpath=self._paths.jinja2_dir),
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
            self.__env.filters["camelcase"] = _to_camel_case
            self.__env.filters["path_join"] = lambda paths: path_join(*paths)  # ruff: ignore[os-path-join]
            self.__env.filters["display_number"] = _to_int_or_float
        return self.__env

    @staticmethod
    def _compile(latex_path: Path, build_dir: Path) -> CompletedProcess:
        command = [
            "latexmk",
            "-pdflatex=xelatex -shell-escape -interaction=nonstopmode %O %S",
            "-dvi-",
            "-ps-",
            "-pdf",
        ]
        command.append(str(latex_path))
        return run(command, cwd=build_dir, capture_output=True, encoding="utf8")

    @staticmethod
    def _setup_link(source: Path, target: Path) -> None:
        if not target.exists():
            msg = (
                f"{target} could not be found. Please make sure it exists before "
                "proceeding."
            )
            raise InvoicezError(msg)
        target = target.resolve()
        if source.is_symlink():
            if source.resolve().samefile(target):
                return
            msg = (
                f"{source} already exists in the build directory and does not point to "
                f"{target}. Please clean the build directory."
            )
            raise InvoicezError(msg)
        if source.exists():
            msg = (
                f"{source} already exists in the build directory. Please clean the "
                "build directory."
            )
            raise InvoicezError(msg)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.symlink_to(target)


def _to_camel_case(string: str) -> str:
    return "".join(substring.capitalize() or "_" for substring in string.split("_"))


def _to_int_or_float(number: int | float) -> str:
    return f"{number:.2f}".rstrip("0").rstrip(".")
