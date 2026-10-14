r"""Dump an anonymised copy of a Google Calendar, for the gcal test fixtures.

Run from the data repo (its settings.yml must have gcal.title_pattern):

    uv run --project ../invoicez-gcal-port --extra gcal \
        ../invoicez-gcal-port/scripts/dump_gcal_fixture.py

Writes events.anonymized.json, settings.anonymized.yml, summary.txt and
mapping.json (pseudonym -> real value, for your eyes only) outside of any git
repository.
"""

import json
from pathlib import Path

from cyclopts import run
from git import InvalidGitRepositoryError, Repo
from platformdirs import user_cache_path
from yaml import safe_dump, safe_load

from invoicez import app_name
from invoicez.exceptions import InvoicezError
from invoicez.gcal.anonymize import (
    anonymize,
    anonymized_settings,
    find_leaks,
    summarize,
)
from invoicez.gcal.google import (
    calendar_service,
    credentials,
    fetch_events,
    list_calendars,
)


def _check_outside_git(output: Path) -> None:
    existing = next(path for path in (output, *output.parents) if path.exists())
    try:
        Repo(str(existing), search_parent_directories=True)
    except InvalidGitRepositoryError:
        return
    msg = (
        f"{output} is inside a git repository: refusing to write mapping.json "
        "(real names) where it could be committed."
    )
    raise InvoicezError(msg)


def _choose_calendar(service: object) -> str:
    calendars = list_calendars(service)
    for index, calendar in enumerate(calendars):
        primary = " (primary)" if calendar.get("primary") else ""
        print(f"{index}. {calendar.get('summary', '?')}{primary}")
    calendar_id = calendars[int(input("Calendar number: "))]["id"]
    print(f"Set gcal.calendar_id: {calendar_id} in settings.yml to skip this step.")
    return calendar_id


def main(
    *,
    settings: Path = Path("settings.yml"),
    calendar_id: str | None = None,
    output: Path | None = None,
) -> None:
    """Dump an anonymised copy of a Google Calendar.

    Args:
        settings: settings.yml with a gcal section (only title_pattern,
            calendar_id and the trainings' rates are read).
        calendar_id: Calendar to dump. Defaults to gcal.calendar_id, else asked.
        output: Directory to write to, outside of any git repository.
    """
    gcal_section = safe_load(settings.read_text(encoding="utf8"))["gcal"]
    output = (output or user_cache_path(app_name) / "gcal-dump").resolve()
    _check_outside_git(output)
    output.mkdir(parents=True, exist_ok=True)

    service = calendar_service(credentials())
    calendar_id = calendar_id or gcal_section.get("calendar_id")
    if calendar_id is None:
        calendar_id = _choose_calendar(service)
    events = fetch_events(service, calendar_id)

    anonymized, pseudonyms = anonymize(events, gcal_section["title_pattern"])
    section, unknown_trainings = anonymized_settings(gcal_section, pseudonyms)

    (output / "events.anonymized.json").write_text(
        json.dumps(anonymized, ensure_ascii=False, indent=2), encoding="utf8"
    )
    (output / "settings.anonymized.yml").write_text(
        safe_dump({"gcal": section}, allow_unicode=True, sort_keys=False),
        encoding="utf8",
    )
    (output / "summary.txt").write_text(
        summarize(anonymized, gcal_section["title_pattern"]), encoding="utf8"
    )
    mapping = output / "mapping.json"
    mapping.touch(mode=0o600, exist_ok=True)
    mapping.chmod(0o600)
    mapping.write_text(
        json.dumps(pseudonyms.mapping, ensure_ascii=False, indent=2), encoding="utf8"
    )

    print(f"Dumped {len(events)} events to {output}")
    print((output / "summary.txt").read_text(encoding="utf8"), end="")
    for training in unknown_trainings:
        print(f"WARNING: no rate found for {training} (left TODO in settings).")
    if leaks := find_leaks(anonymized, pseudonyms.mapping):
        print(
            f"WARNING: {len(leaks)} real value(s) still appear in the events, "
            f"check them before using the dump: {', '.join(map(repr, leaks))}"
        )


if __name__ == "__main__":
    run(main)
