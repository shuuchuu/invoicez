from datetime import date, timedelta
from typing import Any

from pytest import approx, fixture, raises

from invoicez.exceptions import InvoicezError
from invoicez.gcal.grouping import Engagement, group_sessions
from invoicez.gcal.parsing import Session, SessionDescription, parse_events
from invoicez.gcal.pricing import Pricing, number, price, render_template
from invoicez.gcal.settings import GcalSettings
from invoicez.variables import InvoiceLine

# A Monday.
_MONDAY = date(2026, 10, 5)


@fixture
def settings(gcal_settings: GcalSettings) -> GcalSettings:
    return GcalSettings.model_validate(
        {
            **gcal_settings.model_dump(exclude={"rates", "trainings", "templates"}),
            "templates": {
                "description": "{{ days }} jours",
                "day_line": "Journée {{ training.name }} ({{ n }})",
                "half_day_line": "Journée {{ training.name }}, demi ({{ n }})",
                "preparation_line": "Préparation {{ training.name }} ({{ n }})",
            },
            "rates": {"t2": {"day": 690, "preparation_day": 500}, "t1": {"day": 590}},
            "trainings": {
                "orsys": {"bda": {"name": "Big Data", "rate": "t2"}},
                "sparks": {"pav": {"name": "Python avancé", "rate": "t1"}},
            },
        }
    )


def _session(
    day: int,
    *,
    days: int = 1,
    half_day: bool = False,
    company: str = "Orsys",
    training: str = "BDA",
    preparation_days: float = 0,
) -> Session:
    start = _MONDAY + timedelta(days=day)
    return Session(
        event_id=f"id_{day}_{half_day}",
        html_link="link",
        title=f"{company} - {training} - cv",
        continuation=day != 0,
        company=company,
        training=training,
        place="cv",
        extra=None,
        start=start,
        end=start + timedelta(days=days - 1),
        kind="half_day" if half_day else "day",
        quantity=days,
        description=SessionDescription(preparation_days=preparation_days),
    )


def _price(settings: GcalSettings, *sessions: Session) -> Pricing:
    [engagement], _ = group_sessions(sessions)
    pricing, diagnostics = price(engagement, settings)
    assert diagnostics == []
    assert pricing is not None
    return pricing


def _error(settings: GcalSettings, *sessions: Session) -> str:
    [engagement], _ = group_sessions(sessions)
    pricing, [diagnostic] = price(engagement, settings)
    assert pricing is None
    assert diagnostic.level == "error"
    assert diagnostic.event_id == engagement.id
    return diagnostic.message


def _line(
    start: int, end: int | None, n: float, pu: float, description: str
) -> InvoiceLine:
    return InvoiceLine(
        start=_MONDAY + timedelta(days=start),
        end=None if end is None else _MONDAY + timedelta(days=end),
        n=n,
        pu=pu,
        unit="DAY",
        description=description,
    )


def test_block(settings: GcalSettings) -> None:
    pricing = _price(settings, _session(0, days=3))

    assert pricing.company == "orsys"
    assert pricing.training.name == "Big Data"
    assert pricing.days == 3
    assert pricing.lines == (_line(0, 2, 3, 690, "Journée Big Data (3)"),)


def test_single_day_has_no_end(settings: GcalSettings) -> None:
    pricing = _price(settings, _session(0))

    assert pricing.lines == (_line(0, None, 1, 690, "Journée Big Data (1)"),)


def test_one_line_per_run_of_consecutive_days(settings: GcalSettings) -> None:
    # Monday, Wednesday, then Friday to the next Monday as two events.
    pricing = _price(
        settings, _session(0), _session(2), _session(4), _session(7, days=2)
    )

    assert pricing.days == 5
    assert pricing.lines == (
        _line(0, None, 1, 690, "Journée Big Data (1)"),
        _line(2, None, 1, 690, "Journée Big Data (1)"),
        _line(4, None, 1, 690, "Journée Big Data (1)"),
        _line(7, 8, 2, 690, "Journée Big Data (2)"),
    )


def test_days_and_half_days(settings: GcalSettings) -> None:
    pricing = _price(
        settings,
        _session(0, half_day=True),
        _session(1, days=2),
        _session(3, half_day=True),
        _session(4, half_day=True),
    )

    # Half-days are billed as half a day at the day rate, as in the data repo.
    assert pricing.days == approx(3.5)
    assert pricing.lines == (
        _line(0, None, 0.5, 690, "Journée Big Data, demi (0.5)"),
        _line(1, 2, 2, 690, "Journée Big Data (2)"),
        _line(3, 4, 1, 690, "Journée Big Data, demi (1)"),
    )


def test_morning_and_afternoon(settings: GcalSettings) -> None:
    pricing = _price(
        settings,
        _session(0, half_day=True),
        _session(0, half_day=True).model_copy(
            update={"event_id": "afternoon", "continuation": True}
        ),
    )

    assert pricing.lines == (_line(0, None, 1, 690, "Journée Big Data, demi (1)"),)


def test_more_than_a_day_on_a_date(settings: GcalSettings) -> None:
    message = _error(
        settings,
        _session(0, days=2),
        _session(1, half_day=True),
        _session(3),
        _session(3).model_copy(update={"event_id": "again"}),
    )

    assert message.endswith("more than a day billed on 2026-10-06, 2026-10-08")


def test_preparation(settings: GcalSettings) -> None:
    pricing = _price(settings, _session(0, days=2, preparation_days=1.5))

    assert pricing.days == 2
    assert pricing.lines == (
        _line(0, 1, 2, 690, "Journée Big Data (2)"),
        InvoiceLine(
            n=1.5, pu=500, unit="DAY", description="Préparation Big Data (1.5)"
        ),
    )


def test_preparation_without_rate(settings: GcalSettings) -> None:
    message = _error(
        settings, _session(0, company="Sparks", training="PAV", preparation_days=1)
    )

    assert message.endswith("1 preparation days, but rate 't1' has no preparation_day")


def test_lookups_ignore_case(settings: GcalSettings) -> None:
    pricing = _price(settings, _session(0, company="SPARKS", training="Pav"))

    assert pricing.company == "sparks"
    assert pricing.lines[0].pu == 590


def test_unknown_company_or_training(settings: GcalSettings) -> None:
    assert _error(settings, _session(0, company="ENI")).endswith(
        "company 'ENI' missing from gcal.trainings"
    )
    assert _error(settings, _session(0, training="PAV")).endswith(
        "training 'PAV' missing from gcal.trainings.orsys"
    )


def test_render_template() -> None:
    assert render_template("{{ days }} jours", days=number(2.0)) == "2 jours"
    with raises(InvoicezError, match="'dayz' is undefined"):
        render_template("{{ dayz }} jours", days=2)
    with raises(InvoicezError, match="gcal template"):
        render_template("{{ days", days=2)


def test_fixture(raw_events: list[dict[str, Any]], gcal_settings: GcalSettings) -> None:
    engagements: list[Engagement]
    engagements, _ = group_sessions(parse_events(raw_events, gcal_settings)[0])

    priced = [price(engagement, gcal_settings) for engagement in engagements]

    assert [d for _, diagnostics in priced for d in diagnostics] == []
    pricings = [pricing for pricing, _ in priced if pricing is not None]
    assert len(pricings) == 156
    # 444 days in day blocks, 32 half-days, 3 preparation days.
    assert sum(p.days for p in pricings) == 460
    assert sum(line.n for p in pricings for line in p.lines) == 463
    for pricing in pricings:
        for line in pricing.lines:
            assert InvoiceLine.model_validate(line.model_dump()) == line
