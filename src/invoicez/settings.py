from pathlib import Path
from typing import Self

from pydantic import BaseModel
from yaml import safe_load

from invoicez.gcal.settings import GcalSettings
from invoicez.ledger.settings import LedgerSettings


class Settings(BaseModel, extra="forbid", frozen=True):
    quote_template: str
    invoice_template: str
    gcal: GcalSettings | None = None
    ledger: LedgerSettings | None = None

    @classmethod
    def from_path(cls, path: Path) -> Self:
        return cls.model_validate(safe_load(path.read_text(encoding="utf8")))
