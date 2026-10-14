"""Price an engagement: invoice lines from its sessions and the settings' rates."""

from collections import Counter
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel

from invoicez.exceptions import InvoicezError
from invoicez.gcal.diagnostics import Diagnostic
from invoicez.gcal.grouping import Engagement
from invoicez.gcal.settings import GcalSettings, Rate, Training
from invoicez.migration.documents import contiguous_spans
from invoicez.variables import InvoiceLine

# Plain text: lines end up in YAML, then LaTeX-escaped when building the PDF.
_ENVIRONMENT = Environment(undefined=StrictUndefined, autoescape=False)


class Pricing(BaseModel, extra="forbid", frozen=True):
    # Key of the company in settings.yml and variables.yml.
    company: str
    training: Training
    rate: Rate
    # Training days, half-days counting 0.5, preparation days excluded.
    days: float
    lines: tuple[InvoiceLine, ...]


def number(value: float) -> int | float:
    """Return value as an int when whole, so that it renders as 2, not 2.0."""
    return int(value) if float(value).is_integer() else value


def render_template(template: str, **context: Any) -> str:
    """Render a gcal template of settings.yml.

    Returns:
        The rendered text.

    Raises:
        InvoicezError: If the template is invalid or uses an unknown variable.
    """
    try:
        return _ENVIRONMENT.from_string(template).render(context)
    except TemplateError as e:
        msg = f"gcal template {template!r}: {e}"
        raise InvoicezError(msg) from e


def _lookup[V](mapping: Mapping[str, V], key: str) -> tuple[str, V] | None:
    # Titles and settings keys do not share their case (BDA vs bda).
    for candidate, value in mapping.items():
        if candidate.casefold() == key.casefold():
            return candidate, value
    return None


def _billed_days(engagement: Engagement) -> tuple[Counter[date], Counter[date]]:
    # Number of full days and of half-days billed on each date.
    days: Counter[date] = Counter()
    half_days: Counter[date] = Counter()
    for session in engagement.sessions:
        if session.kind == "half_day":
            half_days[session.start] += session.quantity
            continue
        for i in range(session.quantity):
            days[session.start + timedelta(days=i)] += 1
    return days, half_days


def price(
    engagement: Engagement, settings: GcalSettings
) -> tuple[Pricing | None, list[Diagnostic]]:
    """Compute the invoice lines of an engagement.

    One line per kind (day or half-day) and run of consecutive dates, in
    chronological order, then a preparation line without dates. Half-days
    are billed as half a day at the day rate.

    Returns:
        The pricing, or None with an error diagnostic: there is no fallback
        when the company, training or a needed rate is missing, nor when a
        date would be billed more than one day.
    """

    def error(message: str) -> tuple[None, list[Diagnostic]]:
        return None, [
            Diagnostic(
                level="error",
                event_id=engagement.id,
                message=f"{engagement.root.title!r}: {message}",
            )
        ]

    found_company = _lookup(settings.trainings, engagement.company)
    if found_company is None:
        return error(f"company {engagement.company!r} missing from gcal.trainings")
    company, trainings = found_company
    found_training = _lookup(trainings, engagement.training)
    if found_training is None:
        return error(
            f"training {engagement.training!r} missing from gcal.trainings.{company}"
        )
    _, training = found_training
    rate = settings.rates[training.rate]
    preparation_days = engagement.description.preparation_days
    preparation_pu = rate.preparation_day
    if preparation_days and preparation_pu is None:
        return error(
            f"{preparation_days:g} preparation days, but rate {training.rate!r} "
            "has no preparation_day"
        )

    days, half_days = _billed_days(engagement)
    overbooked = sorted(
        str(day) for day in days | half_days if days[day] + half_days[day] / 2 > 1
    )
    if overbooked:
        return error(f"more than a day billed on {', '.join(overbooked)}")

    total = days.total() + half_days.total() / 2
    context = {"company": company, "training": training, "days": number(total)}
    lines: list[InvoiceLine] = []
    for counts, factor, template in (
        (days, 1, settings.templates.day_line),
        (half_days, 0.5, settings.templates.half_day_line),
    ):
        for start, end in contiguous_spans(sorted(counts)):
            n = factor * sum(
                count for day, count in counts.items() if start <= day <= end
            )
            lines.append(
                InvoiceLine(
                    start=start,
                    end=end if end != start else None,
                    n=n,
                    pu=rate.day,
                    unit=settings.unit,
                    description=render_template(
                        template, **context, start=start, end=end, n=number(n)
                    ),
                )
            )
    lines.sort(key=lambda line: line.start or date.min)
    if preparation_days and preparation_pu is not None:
        lines.append(
            InvoiceLine(
                n=preparation_days,
                pu=preparation_pu,
                unit=settings.unit,
                description=render_template(
                    settings.templates.preparation_line,
                    **context,
                    n=number(preparation_days),
                ),
            )
        )
    return Pricing(
        company=company, training=training, rate=rate, days=total, lines=tuple(lines)
    ), []
