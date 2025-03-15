from itertools import chain
from pathlib import Path

from yaml import safe_dump, safe_load

from ..cli import app
from ..config.paths import Paths


@app.command()
def convert_invoices() -> None:
    paths = Paths(Path("."))
    for path in chain(
        paths.gcal_ymls_dir.rglob("*.yml"), paths.extra_ymls_dir.rglob("*.yml")
    ):
        with path.open(encoding="utf8") as fh:
            content = safe_load(fh)

        # Rename products to items
        if "products" in content:
            content["items"] = content.pop("products")

        # Rename pu to unit_price
        for item in content["items"]:
            if "pu" in item:
                item["unit_price"] = item.pop("pu")

        with path.open(mode="w", encoding="utf8") as fh:
            safe_dump(content, fh, allow_unicode=True, default_flow_style=False)
