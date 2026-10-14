"""Group training sessions into engagements, the unit that gets billed."""

import json
from collections.abc import Iterable
from datetime import date, timedelta
from hashlib import sha256

from pydantic import BaseModel

from invoicez.gcal.diagnostics import Diagnostic, Level
from invoicez.gcal.parsing import Session, SessionDescription

# Longer gaps between two sessions of an engagement are likely a continuation
# meant for a later root event.
MAX_GAP = timedelta(days=31)

_Key = tuple[str, str, str, str | None]


class Engagement(BaseModel, extra="forbid", frozen=True):
    """A root session and its continuations, in chronological order."""

    sessions: tuple[Session, ...]

    @property
    def root(self) -> Session:
        return self.sessions[0]

    @property
    def id(self) -> str:
        return self.root.event_id

    @property
    def company(self) -> str:
        return self.root.company

    @property
    def training(self) -> str:
        return self.root.training

    @property
    def place(self) -> str:
        return self.root.place

    @property
    def extra(self) -> str | None:
        return self.root.extra

    @property
    def description(self) -> SessionDescription:
        return self.root.description

    @property
    def start(self) -> date:
        return self.root.start

    @property
    def end(self) -> date:
        return max(session.end for session in self.sessions)

    @property
    def fingerprint(self) -> str:
        """Hash of what is billed, to detect calendar changes after billing.

        Case changes in titles do not change it.
        """
        content = {
            "company": self.company.casefold(),
            "training": self.training.casefold(),
            "preparation_days": self.description.preparation_days,
            "sessions": sorted(
                (s.event_id, str(s.start), str(s.end), s.kind, s.quantity)
                for s in self.sessions
            ),
        }
        dumped = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return sha256(dumped.encode("utf8")).hexdigest()


def _key(session: Session) -> _Key:
    # origin/gcal lowercased titles, so continuations matched whatever the case.
    return (
        session.company.casefold(),
        session.training.casefold(),
        session.place.casefold(),
        session.extra.casefold() if session.extra else None,
    )


def _diagnostic(level: Level, session: Session, message: str) -> Diagnostic:
    return Diagnostic(
        level=level, event_id=session.event_id, message=f"{session.title!r}: {message}"
    )


def group_sessions(
    sessions: Iterable[Session],
) -> tuple[list[Engagement], list[Diagnostic]]:
    """Attach each continuation to the latest earlier root with the same title.

    Titles are compared on company, training, place and extra, ignoring case.

    Returns:
        The engagements, by start date, and the diagnostics: an error for each
        continuation without a root (left out of every engagement), a warning
        for each gap over MAX_GAP and for each continuation description that
        would be ignored.
    """
    groups: dict[_Key, list[Session]] = {}
    closed: list[list[Session]] = []
    diagnostics: list[Diagnostic] = []
    for session in sorted(sessions, key=lambda s: s.start):
        key = _key(session)
        if not session.continuation:
            if key in groups:
                closed.append(groups[key])
            groups[key] = [session]
            continue
        group = groups.get(key)
        if group is None:
            diagnostics.append(
                _diagnostic("error", session, "continuation without a root event")
            )
            continue
        previous_end = max(s.end for s in group)
        if session.start - previous_end > MAX_GAP:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    session,
                    f"starts {(session.start - previous_end).days} days after the "
                    f"previous session of its engagement ({previous_end})",
                )
            )
        if session.description != SessionDescription():
            diagnostics.append(
                _diagnostic(
                    "warning",
                    session,
                    "description of a continuation, ignored: put it on the root event",
                )
            )
        group.append(session)
    closed.extend(groups.values())
    engagements = [Engagement(sessions=tuple(group)) for group in closed]
    return sorted(engagements, key=lambda e: e.start), diagnostics
