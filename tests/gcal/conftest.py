import json
from pathlib import Path
from typing import Any

from pytest import fixture
from yaml import safe_load

from invoicez.gcal.settings import GcalSettings

# The anonymised calendar (billing events plus one-off two-segment ones) and
# settings, from scripts/dump_gcal_fixture.py.
ASSETS_DIR = Path(__file__).parent / "assets"


@fixture(scope="session")
def raw_events() -> list[dict[str, Any]]:
    return json.loads((ASSETS_DIR / "events.json").read_text(encoding="utf8"))


@fixture(scope="session")
def gcal_settings() -> GcalSettings:
    content = safe_load((ASSETS_DIR / "settings.yml").read_text(encoding="utf8"))
    return GcalSettings.model_validate(content["gcal"])
