from typing import Literal

from pydantic import BaseModel


class Diagnostic(BaseModel, extra="forbid", frozen=True):
    """A problem found in calendar data, reported instead of raised.

    Errors block billing what they concern; warnings and infos do not.
    """

    level: Literal["info", "warning", "error"]
    # None when the problem is not about a single event.
    event_id: str | None
    message: str
