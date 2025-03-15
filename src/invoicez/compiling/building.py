from contextlib import suppress
from copy import deepcopy
from filecmp import cmp
from logging import getLogger
from os import unlink
from pathlib import Path
from shutil import copyfile, move
from subprocess import CompletedProcess, run
from tempfile import NamedTemporaryFile

from ..config.paths import Paths
from ..config.settings import Settings
from .target import Target
from .templating import TemplateRenderer


class Builder:
    def __init__(self, target: Target, settings: Settings, paths: Paths):
        self._target = target
        self._settings = settings
        self._paths = paths
        self._template_renderer = TemplateRenderer(settings, paths)
        self._logger = getLogger(__name__)
        self._build()

    def _build(self) -> None:
        build_dir = self._setup_build_dir()
        filename = self._target.name
        latex_path = build_dir / f"{filename}.tex"
        build_pdf_path = latex_path.with_suffix(".pdf")
        output_pdf_path = self._paths.pdfs_dir / f"{filename}.pdf"

        self._write_latex(latex_path)

        completed_process = self._compile(
            latex_path=latex_path.relative_to(build_dir),
            build_dir=build_dir,
        )
        compilation = f"{self._target.name}/{self._target.template_name}"
        if completed_process.returncode == 0:
            self._paths.pdfs_dir.mkdir(parents=True, exist_ok=True)
            copyfile(build_pdf_path, output_pdf_path)
        else:
            self._logger.warning("Compilation %s errored", compilation)
            self._logger.warning(
                "Captured %s stderr\n%s", compilation, completed_process.stderr
            )
            self._logger.warning(
                "Captured %s stdout\n%s", compilation, completed_process.stdout
            )

    def _setup_build_dir(self) -> Path:
        target_build_dir = (
            self._paths.build_dir / self._target.name / self._target.template_name
        )
        target_build_dir.mkdir(parents=True, exist_ok=True)
        return target_build_dir

    def _write_latex(self, output_path: Path) -> None:
        context = deepcopy(self._target.data)
        for k, v in (
            (k, v) for k, v in self._settings.dict().items() if k != "companies"
        ):
            context[k] = v
        context["company"] = {
            **self._settings.companies[self._target.data["company"]].dict(),
        }
        try:
            with NamedTemporaryFile("w", encoding="utf8", delete=False) as fh:
                fh.write(
                    self._template_renderer.render_invoice(
                        str(self._target.template_path.name), **context
                    )
                )
                fh.write("\n")
            if not output_path.exists() or not cmp(fh.name, str(output_path)):
                move(fh.name, output_path)
        finally:
            with suppress(FileNotFoundError):
                unlink(fh.name)

    def _compile(self, latex_path: Path, build_dir: Path) -> CompletedProcess:
        command = self._settings.latex_build_command
        command.append(str(latex_path))
        return run(command, cwd=build_dir, capture_output=True, encoding="utf8")
