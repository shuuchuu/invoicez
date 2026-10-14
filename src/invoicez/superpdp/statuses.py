"""Read the lifecycle of a sent invoice from its SuperPDP events.

Events are not a state machine: each one records that something happened. The ppf:*
ones follow the copy sent to the administration and only matter when they fail.
"""

from datetime import datetime

PAID = "fr:212"
CANCELLED = "fr:220"
INVALID = "api:invalid"
_PROBLEMS = frozenset({INVALID, "api:rejected", "fr:207", "fr:210", "fr:213", "fr:501"})
# Handed to the buyer's platform.
_EMITTED = frozenset({"fr:201", "api:sent"})
# Only the buyer's platform reports these, once it has the invoice.
_DELIVERED = frozenset(
    {
        "api:acknowledged",
        "api:accepted",
        *(f"fr:{code}" for code in range(202, 213)),
    }
)


def is_problem(event: dict) -> bool:
    code = event["status_code"]
    return code in _PROBLEMS or (
        code.startswith("ppf:") and code.endswith(("-ack-error", "-rejected"))
    )


def _shown(event: dict) -> bool:
    return not event["status_code"].startswith("ppf:") or is_problem(event)


def latest_event(invoice: dict) -> dict | None:
    """Return the latest event worth showing, if any.

    Args:
        invoice: Invoice as returned by SuperPDP, with its events.

    Returns:
        The event, or None if the invoice has none yet.
    """
    events = [event for event in invoice.get("events") or [] if _shown(event)]
    if not events:
        return None
    return max(events, key=lambda event: (event["created_at"], event["id"]))


def has_status(invoice: dict, code: str) -> bool:
    return any(event["status_code"] == code for event in invoice.get("events") or [])


def problems(invoice: dict) -> list[dict]:
    return [event for event in invoice.get("events") or [] if is_problem(event)]


def was_transmitted(invoice: dict) -> bool:
    """Tell whether the invoice may have left SuperPDP.

    Only an invoice SuperPDP found invalid before sending it anywhere can be sent
    again under the same number.

    Args:
        invoice: Invoice as returned by SuperPDP, with its events.

    Returns:
        False if its latest event is api:invalid, else True.
    """
    latest = latest_event(invoice)
    return latest is None or latest["status_code"] != INVALID


def describe(event: dict) -> str:
    """Return the status of an event, with its reasons and notes if any.

    Args:
        event: Event of an invoice, as returned by SuperPDP.

    Returns:
        A one-line description.
    """
    text = f"{event['status_text']} ({event['status_code']})"
    extras: list[str] = []
    for detail in event.get("details") or []:
        if detail.get("reason"):
            extras.append(f"reason {detail['reason']}")
        for note in detail.get("notes") or []:
            extras.extend(
                str(value)
                for content in note.get("contents") or []
                for value in content.values()
                if isinstance(value, str) and value
            )
    return f"{text}: {'; '.join(extras)}" if extras else text


def undelivered_since(invoice: dict) -> datetime | None:
    """Return when the invoice went to the buyer's platform, if it never confirmed it.

    Args:
        invoice: Invoice as returned by SuperPDP, with its events.

    Returns:
        When it was handed over, or None if it was not yet, or was received.
    """
    events = invoice.get("events") or []
    if any(event["status_code"] in _DELIVERED for event in events):
        return None
    emitted = [e["created_at"] for e in events if e["status_code"] in _EMITTED]
    return datetime.fromisoformat(min(emitted)) if emitted else None
