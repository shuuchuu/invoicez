from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError

from invoicez.exceptions import InvoicezError
from invoicez.numbering import find_document_path, is_document_number


class Paths:
    def __init__(self) -> None:
        self.working_dir = Path().resolve()
        self.build_dir = self.git_dir / "build"
        # Built documents wait here until they are sent.
        self.sendbox_dir = self.git_dir / "sendbox"
        # Sent invoices are moved here, under <company>/<year>/.
        self.archive_dir = self.git_dir / "archive"
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

    def resolve_document(self, document: str, *, quote: bool = False) -> Path:
        """Return the YAML file of a document given by path or by number.

        Args:
            document: Path of the YAML file, or number of the document (e.g.
                2026-10-003).
            quote: Look the number up among quotes rather than invoices.

        Returns:
            Absolute path of the YAML file.

        Raises:
            InvoicezError: If document is neither an existing YAML file nor a
                known document number.
        """
        path = self.working_dir / document
        if path.is_file():
            if path.suffix not in {".yml", ".yaml"}:
                msg = f"{document!r} is not a YAML document."
                raise InvoicezError(msg)
            return path
        if is_document_number(document):
            return find_document_path(self.git_dir, document, quote=quote)
        msg = f"{document!r} is neither a file nor a document number."
        raise InvoicezError(msg)
