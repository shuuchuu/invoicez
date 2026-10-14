from typing import Literal

from pydantic import BaseModel

Level = Literal["info", "warning", "error"]


class Diagnostic(BaseModel, extra="forbid", frozen=True):
    """A problem found in calendar data, reported instead of raised.

    Errors block billing what they concern; warnings and infos do not.
    """

    level: Level
    # None when the problem is not about a single event.
    event_id: str | None
    message: str
