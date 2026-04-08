from typing import Any

from yaml import safe_load as yaml_safe_load

from invoicez.paths import Paths


def get_config(paths: Paths) -> dict[str, Any]:
    return yaml_safe_load(paths.config.read_text(encoding="utf8"))
