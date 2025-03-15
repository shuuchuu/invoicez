from datetime import date
from logging import getLogger

from pydantic import BaseModel
from yaml import safe_load as yaml_safe_load

from ..model.data import Company, Rate, Training
from .paths import Paths

_logger = getLogger(__name__)


class Settings(BaseModel):
    model_config = {"extra": "allow"}

    latex_build_command: list[str]
    start_date: date = date.min
    invoice_name_format_string: str
    title_pattern: str
    companies: dict[str, Company]
    trainings: dict[str, Training]
    rates: dict[str, Rate]

    @classmethod
    def load(cls, paths: Paths) -> "Settings":
        data = yaml_safe_load(paths.config.read_text(encoding="utf8"))
        data["companies"] = yaml_safe_load(
            paths.companies_config_data.read_text(encoding="utf8")
        )
        data["trainings"] = yaml_safe_load(
            paths.trainings_config_data.read_text(encoding="utf8")
        )
        data["rates"] = yaml_safe_load(
            paths.rates_config_data.read_text(encoding="utf8")
        )
        return cls.model_validate(data)
