import sys
from datetime import datetime
from pathlib import Path
from shutil import copytree
from typing import Any

import appdirs
from git import Repo
from pdfminer.high_level import extract_pages, extract_text
from pytest import fixture, mark
from yaml import safe_load as yaml_safe_load


@fixture
def working_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    data_dir = Path(__file__).parent / __name__
    tmp_dir = tmp_path / "data"
    copytree(data_dir, tmp_dir)
    Repo.init(str(tmp_dir))
    working_dir = tmp_dir / "company"
    monkeypatch.chdir(working_dir)
    monkeypatch.setattr(appdirs, "user_config_dir", lambda _: str(tmp_dir))
    return working_dir


@mark.parametrize("args,n_pages", [(["2020-10-001.yml"], 1)])
def test_run(
    working_dir: Path, monkeypatch: Any, args: list[str], n_pages: int
) -> None:
    from invoicez.cli import cli

    monkeypatch.setattr(sys, "argv", ["invoicez", "run", *args])

    try:
        cli()
    except SystemExit as e:
        if e.code != 0:
            raise e

    with (working_dir / "pdf" / "2020-10-001.pdf").open("rb") as fh:
        pages = list(extract_pages(fh))
    with (working_dir / "pdf" / "2020-10-001.pdf").open("rb") as fh:
        text = extract_text(fh)
    assert len(pages) == n_pages
    assert "John Doe" in text


def test_new_quote(working_dir: Path, monkeypatch: Any) -> None:
    from invoicez.cli import cli

    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    path_invoice = working_dir / f"{prefix}-001.yml"
    path_quote = working_dir / f"quote-{prefix}-001.yml"

    monkeypatch.setattr(
        sys, "argv", ["invoicez", "new", "--quote", "2020-10-001.yml", "_"]
    )
    try:
        cli()
    except SystemExit as e:
        if e.code != 0:
            raise e

    assert path_quote.exists()

    monkeypatch.setattr(sys, "argv", ["invoicez", "new", str(path_quote), "_"])
    try:
        cli()
    except SystemExit as e:
        if e.code != 0:
            raise e

    assert path_invoice.exists()
    content = yaml_safe_load(path_invoice.read_text(encoding="utf8"))
    assert "quote_number" in content
    assert content["quote_number"] == f"{prefix}-001"
