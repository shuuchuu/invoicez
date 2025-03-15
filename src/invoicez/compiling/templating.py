from collections.abc import Iterable, Sequence
from datetime import date
from logging import getLogger
from os.path import join as path_join
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from ..config.paths import Paths
from ..config.settings import Settings
from ..model.data import Company, Training
from ..model.invoice import Item
from ..model.merged_event import MergedEvent


class TemplateRenderer:
    def __init__(self, settings: Settings, paths: Paths):
        self._settings = settings
        self._paths = paths
        self._logger = getLogger(__name__)
        self._env = Environment(
            loader=FileSystemLoader(searchpath=self._paths.templates_dir),
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
        self._env.filters["camelcase"] = self._to_camel_case
        self._env.filters["path_join"] = lambda paths: path_join(*paths)
        self._env.filters["display_number"] = self._to_int_or_float
        self._env.filters["pretty_print_dates"] = self._format_dates

    def render_invoice(
        self,
        invoice_number: str,
        ref: str,
        date: date,
        company: Company,
        training: Training,
        items: list[Item],
    ) -> str:
        return self._render(
            self._paths.invoice_template,
            invoice_number=invoice_number,
            ref=ref,
            date=date,
            company=company.model_dump(),
            training=training.model_dump(),
            items=[item.model_dump() for item in items],
        )

    def render_invoice_description(
        self, items: list[Item], company: Company, training: Training
    ) -> str:
        return self._render(
            self._paths.invoice_description_template,
            company=company.model_dump(),
            training=training.model_dump(),
            items=[item.model_dump() for item in items],
        )

    def render_invoice_main_item_description(
        self, merged_event: MergedEvent, company: Company, training: Training
    ) -> str:
        return self._render(
            self._paths.invoice_main_item_description_template,
            merged_event=merged_event.model_dump(),
            company=company.model_dump(),
            training=training.model_dump(),
        )

    def render_dates(self, dates: Iterable[date]) -> str:
        return self._render(self._paths.dates_template, dates=sorted(set(dates)))

    def _render(self, path: Path, /, **context: Any) -> str:
        return self._env.get_template(path.name).render(context).strip()

    def _to_camel_case(self, string: str) -> str:
        return "".join(substring.capitalize() or "_" for substring in string.split("_"))

    def _to_int_or_float(self, number: int | float) -> str:
        return f"{number:.2f}".rstrip("0").rstrip(".")

    @classmethod
    def _format_dates(cls, dates: Iterable[date]) -> str:
        return r"\\".join(
            cls._format_date_group(group) for group in cls._group_dates(dates)
        )

    @classmethod
    def _group_dates(cls, dates: Iterable[date]) -> list[list[date]]:
        groups = []
        current_group: list[date] = []
        for d in sorted(set(dates)):
            if not current_group:
                current_group.append(d)
            else:
                if d.toordinal() - current_group[-1].toordinal() == 1:
                    current_group.append(d)
                else:
                    groups.append(current_group)
                    current_group = [d]
        if current_group:
            groups.append(current_group)
        return groups

    @classmethod
    def _format_date_group(cls, dates: Sequence[date]) -> str:
        start = dates[0]
        if len(dates) == 1:
            return f"{start.day}/{start.month}/{start.year}"
        end = dates[-1]
        if start.year == end.year:
            if start.month == end.month:
                return f"{start.day}–{end.day}/{start.month}/{start.year}"  # noqa: RUF001
            return f"{start.day}/{start.month}–{end.day}/{end.month}/{start.year}"  # noqa: RUF001
        return (
            f"{start.day}/{start.month}/{start.year}–{end.day}/{end.month}/{end.year}"  # noqa: RUF001
        )
