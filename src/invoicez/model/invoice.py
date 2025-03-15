"""Data classes related to invoices."""

from collections.abc import Iterator
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from yaml import safe_dump, safe_load


class GcalInfo(BaseModel):
    link: str
    event_id: str


class Item(BaseModel):
    dates: str | None
    description: str
    n: int
    unit_price: float


class Invoice(BaseModel):
    path: Path
    invoice_number: str
    description: str
    company: str
    ref: str
    date: date
    gcal_info: GcalInfo | None
    items: list[Item]

    @classmethod
    def all_from_ymls_dir(cls, ymls_dir: Path) -> Iterator["Invoice"]:
        for path in sorted(ymls_dir.glob("*.yml")):
            data = safe_load(path.read_text(encoding="utf8"))
            data["path"] = path
            yield cls.model_validate(data)

    def dct(self) -> dict[str, Any]:
        return self.model_dump(
            exclude={"path"},
            exclude_unset=True,
            exclude_none=True,
            exclude_defaults=True,
        )

    def yml(self) -> str:
        with StringIO() as string_io:
            safe_dump(
                self.dct(), string_io, allow_unicode=True, default_flow_style=False
            )
            return string_io.getvalue()

    def write(self) -> None:
        with self.path.open(mode="w", encoding="utf8") as fh:
            fh.write(self.yml())
