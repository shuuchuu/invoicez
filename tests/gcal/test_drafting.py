from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pytest import fixture, raises
from yaml import safe_dump, safe_load

from invoicez.exceptions import InvoicezError
from invoicez.gcal.drafting import draft, draft_path
from invoicez.gcal.grouping import Engagement, group_sessions
from invoicez.gcal.parsing import Session, SessionDescription, parse_events
from invoicez.gcal.pricing import Pricing, price
from invoicez.gcal.settings import GcalSettings
from invoicez.settings import Settings
from invoicez.variables import Variables

_VARIABLES = Path(__file__).parents[1] / "test_cli" / "variables.yml"
_TODAY = date(2026, 10, 14)


@fixture
def global_mapping() -> dict[str, Any]:
    mapping = safe_load(_VARIABLES.read_text(encoding="utf8"))
    company = mapping["companies"]["company1"]
    mapping["companies"] = {"orsys": {**company, "name": "Orsys"}}
    return mapping


@fixture
def settings(gcal_settings: GcalSettings) -> GcalSettings:
    return GcalSettings.model_validate(
        {
            **gcal_settings.model_dump(exclude={"rates", "trainings"}),
            "rates": {"t2": {"day": 690, "preparation_day": 500}},
            "trainings": {"orsys": {"bda": {"name": "Big Data", "rate": "t2"}}},
        }
    )


def _engagement(preparation_days: float = 0) -> Engagement:
    start = date(2026, 10, 5)
    sessions = [
        Session(
            event_id="root",
            html_link="link",
            title="Orsys - BDA - cv",
            continuation=False,
            company="Orsys",
            training="BDA",
            place="cv",
            extra=None,
            start=start,
            end=start + timedelta(days=1),
            kind="day",
            quantity=2,
            description=SessionDescription(
                ref="BDAZZ1", preparation_days=preparation_days
            ),
        ),
        Session(
            event_id="next",
            html_link="link",
            title="-> Orsys - BDA - cv",
            continuation=True,
            company="Orsys",
            training="BDA",
            place="cv",
            extra=None,
            start=start + timedelta(days=7),
            end=start + timedelta(days=7),
            kind="half_day",
            quantity=1,
            description=SessionDescription(),
        ),
    ]
    [engagement], _ = group_sessions(sessions)
    return engagement


def _priced(engagement: Engagement, settings: GcalSettings) -> Pricing:
    pricing, _ = price(engagement, settings)
    assert pricing is not None
    return pricing


def _draft(
    engagement: Engagement,
    settings: GcalSettings,
    global_mapping: dict[str, Any],
    reference: str | None = "BDAZZ1",
) -> dict[str, Any]:
    return draft(
        engagement,
        _priced(engagement, settings),
        document_number="2026-10-004",
        today=_TODAY,
        reference=reference,
        settings=settings,
        global_mapping=global_mapping,
        calendar_id="calendar@group.calendar.google.com",
    )


def test_draft(settings: GcalSettings, global_mapping: dict[str, Any]) -> None:
    engagement = _engagement(preparation_days=1)

    document = _draft(engagement, settings, global_mapping)

    assert document == {
        "document_number": "2026-10-004",
        "date": "2026-10-14",
        "company": "orsys",
        "description": "Facture pour 2,5 jours de formation « Big Data ».",
        "reference": "BDAZZ1",
        "products": [
            {
                "start": date(2026, 10, 5),
                "end": date(2026, 10, 6),
                "n": 2,
                "pu": 690,
                "unit": "DAY",
                "description": "Journée de formation",
            },
            {
                "start": date(2026, 10, 12),
                "n": 0.5,
                "pu": 690,
                "unit": "DAY",
                "description": "Journée de formation",
            },
            {"n": 1, "pu": 500, "unit": "DAY", "description": "Journée de préparation"},
        ],
        "source": {
            "kind": "gcal",
            "calendar_id": "calendar@group.calendar.google.com",
            "event_ids": ["root", "next"],
            "fingerprint": engagement.fingerprint,
        },
    }


def test_draft_survives_a_yaml_round_trip(
    settings: GcalSettings, global_mapping: dict[str, Any]
) -> None:
    document = _draft(_engagement(), settings, global_mapping)

    dumped = safe_dump(document, allow_unicode=True)
    variables = Variables.from_mappings(global_mapping, safe_load(dumped))

    assert "n: 2\n" in dumped
    assert "start: 2026-10-05\n" in dumped
    assert variables.reference == "BDAZZ1"
    assert variables.source is not None
    assert variables.source.event_ids == ["root", "next"]


def test_draft_without_reference(
    settings: GcalSettings, global_mapping: dict[str, Any]
) -> None:
    document = _draft(_engagement(), settings, global_mapping, reference=None)

    assert "reference" not in document


def test_invalid_drafts(settings: GcalSettings, global_mapping: dict[str, Any]) -> None:
    with raises(InvoicezError, match=r"company 'orsys' missing from variables\.yml"):
        _draft(_engagement(), settings, {**global_mapping, "companies": {}})
    del global_mapping["notices"]
    with raises(InvoicezError, match="invalid draft for 'Orsys - BDA - cv'"):
        _draft(_engagement(), settings, global_mapping)


def test_draft_path(
    settings: GcalSettings, global_mapping: dict[str, Any], tmp_path: Path
) -> None:
    document = _draft(_engagement(), settings, global_mapping)

    path = draft_path(
        document,
        name="BDAZZ1",
        settings=Settings(
            quote_template="Devis-{document_number}-{company}-{name}",
            invoice_template="Facture-{document_number}-{company}-{name}",
            gcal=settings,
        ),
        global_mapping=global_mapping,
        git_dir=tmp_path,
    )

    assert path == Path("gcal-ymls/2026/Facture-2026-10-004-Orsys-BDAZZ1.yml")


def test_draft_path_without_gcal_settings(
    settings: GcalSettings, global_mapping: dict[str, Any], tmp_path: Path
) -> None:
    document = _draft(_engagement(), settings, global_mapping)

    with raises(InvoicezError, match="no gcal section"):
        draft_path(
            document,
            name="BDAZZ1",
            settings=Settings(quote_template="q", invoice_template="i"),
            global_mapping=global_mapping,
            git_dir=tmp_path,
        )


def test_fixture(
    raw_events: list[dict[str, Any]],
    gcal_settings: GcalSettings,
    global_mapping: dict[str, Any],
) -> None:
    company = global_mapping["companies"]["orsys"]
    global_mapping["companies"] = {
        key: {**company, "name": key.capitalize()} for key in gcal_settings.trainings
    }
    engagements, _ = group_sessions(parse_events(raw_events, gcal_settings)[0])

    documents = [
        draft(
            engagement,
            _priced(engagement, gcal_settings),
            document_number=f"2026-10-{i:03}",
            today=_TODAY,
            # Pseudonyms (REF_0) hold a "_", which LaTeX would need escaped.
            reference=(engagement.description.ref or "").replace("_", "-") or None,
            settings=gcal_settings,
            global_mapping=global_mapping,
            calendar_id="calendar-id",
        )
        for i, engagement in enumerate(engagements, start=1)
    ]

    assert len(documents) == 157
    assert sum("reference" in document for document in documents) == 33


def test_draft_refuses_unescaped_latex(
    settings: GcalSettings, global_mapping: dict[str, Any]
) -> None:
    with raises(InvoicezError) as error:
        _draft(_engagement(), settings, global_mapping, reference="CF1 & CF2")

    assert str(error.value) == (
        "the PDF would not build: reference 'CF1 & CF2' has an unescaped '&' "
        "(write \\&)"
    )
    # Escaped characters and LaTeX commands are fine.
    _draft(_engagement(), settings, global_mapping, reference=r"CF1 \& CF2")
    named = settings.model_copy(
        update={
            "trainings": {
                "orsys": {
                    "bda": settings.trainings["orsys"]["bda"].model_copy(
                        update={"name": r"\textit{Big} Data_Science"}
                    )
                }
            }
        }
    )
    with raises(InvoicezError, match=r"description .* has an unescaped '_'"):
        _draft(_engagement(), named, global_mapping)
