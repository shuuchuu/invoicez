from datetime import date, datetime

from pytest import fixture

from invoicez.compiling.templating import TemplateRenderer
from invoicez.config.paths import Paths
from invoicez.config.settings import Settings
from invoicez.model.data import Company, Training
from invoicez.model.duration import Duration, DurationUnit
from invoicez.model.merged_event import MergedEvent


@fixture
def template_renderer(settings: Settings, paths: Paths) -> TemplateRenderer:
    return TemplateRenderer(settings, paths)


def test_invoice_description(template_renderer: TemplateRenderer) -> None:
    assert (
        template_renderer.render_invoice_description(
            items=[],
            company=Company(
                name="Company 1", siren="SIREN", address="Company 1 adress"
            ),
            training=Training(
                name="Training 1",
                short_name="Training 1 short name",
                reference="tr1ref",
                rate="r1",
                company="company1",
            ),
        )
        == "Invoice for the “Training 1” training session"
    )


def test_invoice_main_item_description(template_renderer: TemplateRenderer) -> None:
    assert (
        template_renderer.render_invoice_main_item_description(
            merged_event=MergedEvent(
                company="company1",
                training="training1",
                place="cv",
                description="",
                link="",
                id="",
                starts=[datetime(year=2012, month=12, day=21)],
                durations=[Duration(n=2, unit=DurationUnit.day)],
            ),
            company=Company(
                name="Company 1", siren="SIREN", address="Company 1 adress"
            ),
            training=Training(
                name="Training 1",
                short_name="Training 1 short name",
                reference="tr1ref",
                rate="r1",
                company="company1",
            ),
        )
        == "Day of “Training 1 short name” training"
    )


def test_dates(template_renderer: TemplateRenderer) -> None:
    assert template_renderer.render_dates([date(2023, 1, 1)]) == "1/1/2023"
    assert (
        template_renderer.render_dates([date(2023, 1, 1), date(2023, 1, 2)])
        == "1–2/1/2023"  # noqa: RUF001
    )
    assert (
        template_renderer.render_dates([date(2023, 1, 31), date(2023, 2, 1)])
        == "31/1–1/2/2023"  # noqa: RUF001
    )
    assert (
        template_renderer.render_dates([date(2023, 12, 31), date(2024, 1, 1)])
        == "31/12/2023–1/1/2024"  # noqa: RUF001
    )
