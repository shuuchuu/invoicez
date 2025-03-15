from datetime import date, timedelta
from logging import getLogger
from typing import Literal

from rich.prompt import Prompt
from yaml import safe_load

from ..config.paths import Paths
from ..config.settings import Settings
from ..exceptions import InvoicezError
from ..model.data import Company, Rate, Training
from ..model.duration import DurationUnit
from ..model.invoice import GcalInfo, Invoice, Item
from ..model.merged_event import MergedEvent
from .templating import TemplateRenderer


class Biller:
    def __init__(self, paths: Paths, settings: Settings) -> None:
        self._paths = paths
        self._settings = settings
        self._logger = getLogger(__name__)
        self._template_renderer = TemplateRenderer(settings, paths)

    def bill(self, merged_event: MergedEvent) -> Invoice:
        if merged_event.description:
            try:
                extra = safe_load(merged_event.description)
            except Exception:
                self._logger.warning(
                    f"Could not parse the description of event {merged_event} as yml."
                )
                extra = {}
        else:
            extra = {}

        invoice_number = self._get_next_invoice_number()
        if "ref" in extra:
            ref = extra["ref"]
        else:
            ref = Prompt.ask(
                "Enter the reference that we should mention on the invoice"
            )
        training = self._settings.trainings[merged_event.training]
        company = self._settings.companies[merged_event.company]
        items = self._produce_main_items(merged_event, company, training)
        description = self._template_renderer.render_invoice_description(
            items, company, training
        )
        path = (
            self._paths.gcal_ymls_dir
            / self._settings.invoice_name_format_string.format(
                invoice_number=invoice_number,
                training=training,
                company=company,
                ref=ref,
            )
        ).with_suffix(".yml")
        return Invoice(
            path=path,
            invoice_number=invoice_number,
            company=merged_event.company,
            description=description,
            ref=ref,
            date=date.today(),
            gcal_info=GcalInfo(link=merged_event.link, event_id=merged_event.id),
            items=items,
        )

    def _get_from_config(
        self, category: Literal["trainings", "companies", "rates"], value: str
    ) -> Training | Company | Rate:
        try:
            return getattr(self._settings, category)[value]
        except KeyError as e:
            msg = f"The value {value} could not be found in {category} config."
            raise InvoicezError(msg) from e

    def _produce_main_items(
        self, merged_event: MergedEvent, company: Company, training: Training
    ) -> list[Item]:
        training = self._settings.trainings[merged_event.training]
        try:
            rates = self._settings.rates[training.rate]
        except KeyError as e:
            msg = f"The rate named “{training.rate}” was not found in the settings."
            raise InvoicezError(msg) from e

        items = []

        half_days = self._filter_merged_event(merged_event, DurationUnit.half_day)
        whole_days = self._filter_merged_event(merged_event, DurationUnit.day)

        if half_days:
            items.append(
                self._produce_main_item(
                    merged_event=half_days,
                    unit_price=rates.training_day // 2,
                    company=company,
                    training=training,
                )
            )
        if whole_days:
            items.append(
                self._produce_main_item(
                    merged_event=whole_days,
                    unit_price=rates.training_day,
                    company=company,
                    training=training,
                )
            )
        return items

    def _produce_main_item(
        self,
        merged_event: MergedEvent,
        unit_price: float,
        company: Company,
        training: Training,
    ) -> Item:
        return Item(
            dates=self._template_renderer.render_dates(
                (start + timedelta(days=i)).date()
                for start, duration in zip(
                    merged_event.starts, merged_event.durations, strict=True
                )
                for i in range(duration.n)
            ),
            n=sum(duration.n for duration in merged_event.durations),
            description=self._template_renderer.render_invoice_main_item_description(
                merged_event, company, training
            ),
            unit_price=unit_price,
        )

    def _get_next_invoice_number(self) -> str:
        now = date.today()
        prefix = now.strftime("%Y-%m")
        n_invoices = 1
        for item in self._paths.git_dir.glob("**/*.yml"):
            with item.open(encoding="utf8") as fh:
                content = safe_load(fh)
            if "invoice_number" in content and content["invoice_number"].startswith(
                prefix
            ):
                n_invoices += 1
        return f"{now.strftime('%Y-%m')}-{n_invoices:03}"

    @staticmethod
    def _filter_merged_event(
        merged_event: MergedEvent, duration_unit: DurationUnit
    ) -> MergedEvent | None:
        result = merged_event.model_copy()
        result.starts = [
            start
            for start, duration in zip(
                merged_event.starts, merged_event.durations, strict=True
            )
            if duration.unit is duration_unit
        ]
        if not result.starts:
            return None
        result.durations = [
            duration
            for duration in merged_event.durations
            if duration.unit is duration_unit
        ]
        return result
