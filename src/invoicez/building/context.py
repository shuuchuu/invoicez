from copy import deepcopy
from typing import Any


def create_context(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    context = deepcopy(data)
    for k, v in ((k, v) for k, v in config.items() if k != "companies"):
        context[k] = v
    context["company"] = {
        **config["company"],
        **config["companies"][data["company"]],
    }
    return context
