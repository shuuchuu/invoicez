from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError

from ..exceptions import InvoicezError


class Paths:
    def __init__(self, working_dir: Path) -> None:
        self.working_dir = working_dir.resolve()

        self.pdfs_dir = self.git_dir / "pdfs"
        self.gcal_ymls_dir = self.git_dir / "gcal-ymls"
        self.extra_ymls_dir = self.git_dir / "extra-ymls"
        self.templates_dir = self.git_dir / "templates"
        self.invoice_description_template = (
            self.templates_dir / "invoice-description.tex"
        )
        self.invoice_main_item_description_template = (
            self.templates_dir / "invoice-main-item-description.tex"
        )
        self.dates_template = self.templates_dir / "dates.tex"
        self.invoice_template = self.templates_dir / "invoice.tex"
        self.config = self.git_dir / "config.yml"
        self.config_data_dir = self.git_dir / "data"
        self.rates_config_data = self.config_data_dir / "rates.yml"
        self.places_config_data = self.config_data_dir / "places.yml"
        self.companies_config_data = self.config_data_dir / "companies.yml"
        self.trainings_config_data = self.config_data_dir / "trainings.yml"

        # Hidden stuff
        self.invoicez_dir = self.git_dir / ".invoicez"
        self.build_dir = self.invoicez_dir / ".build"
        self.gcalendar_secrets = self.invoicez_dir / "gcalendar-secrets.json"
        self.gcalendar_selected_calendar = self.invoicez_dir / "selected-calendar.txt"
        self.gcalendar_credentials = self.invoicez_dir / "gcalendar-credentials.pickle"

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
