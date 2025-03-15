from datetime import datetime
from pathlib import Path

from pytest import fixture

from invoicez.compiling.billing import Biller
from invoicez.config.paths import Paths
from invoicez.config.settings import Settings
from invoicez.model.duration import Duration, DurationUnit
from invoicez.model.merged_event import MergedEvent


@fixture
def biller() -> Biller:
    paths = Paths(Path.cwd())
    return Biller(paths, Settings.load(paths))


def test_billing(biller: Biller) -> None:
    invoice = biller.bill(
        MergedEvent(
            company="company1",
            training="c1_tr1",
            place="1",
            description="ref: REF1",
            link="link_1",
            id="id_1",
            starts=[datetime(2023, 1, 1)],
            durations=[Duration(n=4, unit=DurationUnit.day)],
        )
    )
    print(invoice)
    assert len(invoice.items) == 1
    assert invoice.items[0].n == 4
    assert invoice.items[0].unit_price == 200
    assert (
        invoice.items[0].description
        == "Day of “company 1 training 1 short name” training"
    )
    assert invoice.items[0].dates == "1–4/1/2023"  # noqa: RUF001
    assert invoice.ref == "REF1"
    assert (
        invoice.description
        == "Invoice for the “company 1 training 1 long name” training session"
    )
