from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError

from invoicez.exceptions import InvoicezError


class Paths:
    def __init__(self) -> None:
        self.working_dir = Path().resolve()
        self.build_dir = self.working_dir / "build"
        self.pdf_dir = self.working_dir / "pdf"
        self.assets_dir = self.git_dir / "assets"
        self.templates_dir = self.git_dir / "templates"
        self.jinja2_dir = self.templates_dir / "jinja2"
        self.variables = self.git_dir / "variables.yml"
        self.settings = self.git_dir / "settings.yml"

    @property
    def git_dir(self) -> Path:
        if not hasattr(self, "_git_dir"):
            try:
                repository = Repo(str(self.working_dir), search_parent_directories=True)
            except InvalidGitRepositoryError as e:
                msg = (
                    "Could not find the path of the current git working directory. "
                    "Are you in one?"
                )
                raise InvoicezError(msg) from e
            self._git_dir = Path(repository.git.rev_parse("--show-toplevel")).resolve()
        return self._git_dir
