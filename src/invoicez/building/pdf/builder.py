from logging import getLogger
from pathlib import Path
from subprocess import CompletedProcess, run

from jinja2 import Environment

from invoicez.exceptions import InvoicezError
from invoicez.variables import Variables

_logger = getLogger(__name__)


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


def _setup_build_dir(
    build_dir: Path, assets_dir: Path, name: str, template_name: str
) -> Path:
    target_build_dir = build_dir / name / template_name
    target_build_dir.mkdir(parents=True, exist_ok=True)
    if assets_dir.is_dir():
        for item in assets_dir.iterdir():
            _setup_link(target_build_dir / item.name, item)
    return target_build_dir


def _write_latex(
    output_path: Path,
    environment: Environment,
    template_path: str,
    variables: Variables,
) -> None:
    template = environment.get_template(template_path)
    print(variables.model_dump())
    content = f"{template.render(variables.model_dump())}\n"
    if not output_path.exists() or output_path.read_text(encoding="utf8") != content:
        output_path.write_text(content, encoding="utf8")


def build_pdf(
    name: str,
    template_name: str,
    template_path: str,
    variables: Variables,
    environment: Environment,
    build_dir: Path,
    assets_dir: Path,
) -> bytes:
    build_dir = _setup_build_dir(
        build_dir,
        assets_dir,
        name,
        template_name,
    )
    latex_path = build_dir / f"{name}.tex"
    build_pdf_path = latex_path.with_suffix(".pdf")

    _write_latex(latex_path, environment, template_path, variables)

    completed_process = _compile(
        latex_path=latex_path.relative_to(build_dir),
        build_dir=build_dir,
    )
    compilation = f"{name}/{template_name}"
    if completed_process.returncode != 0:
        _logger.warning("Compilation %s errored", compilation)
        _logger.warning("Captured %s stderr\n%s", compilation, completed_process.stderr)
        _logger.warning("Captured %s stdout\n%s", compilation, completed_process.stdout)
        msg = "Could not complete PDF compilation."
        raise InvoicezError(msg)
    return build_pdf_path.read_bytes()
