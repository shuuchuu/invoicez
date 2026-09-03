from datetime import datetime
from pathlib import Path
from shutil import copytree
from typing import Any
from xml.etree import ElementTree

from facturx import get_xml_from_pdf
from git import Repo
from pdfminer.high_level import extract_pages, extract_text
from pytest import fixture, mark, raises
from yaml import safe_load
from yaml import safe_load as yaml_safe_load

from invoicez.building.xml.builder import build_xml
from invoicez.building.xml.variables_to_bt_converter import convert
from invoicez.cancellation import resolve_cancellation
from invoicez.paths import Paths
from invoicez.variables import Variables

DATA_DIR = Path(__file__).parent / __name__

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": (
        "urn:un:unece:uncefact:data:standard:"
        "ReusableAggregateBusinessInformationEntity:100"
    ),
}


def _embedded_xml(pdf_path: Path) -> ElementTree.Element:
    _, xml_bytes = get_xml_from_pdf(pdf_path.read_bytes())
    assert xml_bytes is not None
    return ElementTree.fromstring(xml_bytes)


@fixture(autouse=True)
def working_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    tmp_dir = tmp_path / "data"
    copytree(DATA_DIR, tmp_dir)
    Repo.init(str(tmp_dir))
    monkeypatch.chdir(tmp_dir)
    return tmp_dir


@mark.parametrize(
    "document_path,n_pages", [(Path("company1/2020-10-001-Company.yml"), 1)]
)
def test_run(document_path: Path, n_pages: int) -> None:
    from invoicez.cli import main

    with raises(SystemExit):
        main("run", str(document_path))

    paths = Paths()

    pdf_path = (paths.pdf_dir / document_path.name).with_suffix(".pdf")
    with pdf_path.open("rb") as fh:
        pages = list(extract_pages(fh))
    with pdf_path.open("rb") as fh:
        text = extract_text(fh)
    assert len(pages) == n_pages
    assert "Burger Queen" in text

    root = _embedded_xml(pdf_path)
    seller_name = root.find(
        ".//ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/ram:Name", NS
    )
    assert seller_name is not None
    assert seller_name.text == "Burger Queen"


def test_new() -> None:
    from invoicez.cli import main

    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    paths = Paths()
    path_invoice = paths.working_dir / f"{prefix}-001.yml"
    path_quote = paths.working_dir / f"quote-{prefix}-001.yml"

    with raises(SystemExit):
        main("new", "--quote", "company1/2020-10-001-Company.yml", "_")

    assert path_quote.exists()
    content_quote = yaml_safe_load(path_quote.read_text(encoding="utf8"))
    assert content_quote["document_number"] == f"{prefix}-001"
    assert content_quote["quote"]

    with raises(SystemExit):
        main("new", str(path_quote), "_")

    assert path_invoice.exists()

    content_invoice = yaml_safe_load(path_invoice.read_text(encoding="utf8"))
    assert not content_invoice.get("quote", False)
    assert content_invoice["document_number"] == f"{prefix}-001"


def test_cancel(working_dir: Path) -> None:
    from invoicez.cli import main

    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    path_cancel = working_dir / f"{prefix}-001-Company.yml"

    with raises(SystemExit):
        main("cancel", "company1/2020-10-001-Company.yml")

    assert path_cancel.exists()
    content = yaml_safe_load(path_cancel.read_text(encoding="utf8"))
    assert content == {
        "document_number": f"{prefix}-001",
        "date": now.strftime("%Y-%m-%d"),
        "cancels": "2020-10-001",
    }

    with raises(SystemExit):
        main("run", path_cancel.name)

    pdf_path = working_dir / "pdf" / path_cancel.with_suffix(".pdf").name
    with pdf_path.open("rb") as fh:
        text = extract_text(fh)
    assert "Avoir pour" in text

    root = _embedded_xml(pdf_path)
    type_code = root.find(".//rsm:ExchangedDocument/ram:TypeCode", NS)
    assert type_code is not None
    assert type_code.text == "381"

    line_name = root.find(".//ram:SpecifiedTradeProduct/ram:Name", NS)
    assert line_name is not None
    assert line_name.text == (
        "Avoir pour « Journée de formation très spécifique basée sur BDA »"  # ruff: ignore[ambiguous-unicode-character-string]
    )


@mark.parametrize(
    "document_path,expected_path",
    [
        (
            Path("company2/2020-10-002-Entreprise client.yml"),
            Path("company2/bt-2020-10-002-Entreprise client.yml"),
        ),
        (
            Path("company2/2020-10-003-Entreprise client.yml"),
            Path("company2/bt-2020-10-003-Entreprise client.yml"),
        ),
    ],
)
def test_generate_data_dict(document_path: Path, expected_path: Path) -> None:
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    document_data = safe_load(Path(document_path).read_text(encoding="utf8"))
    document_data = resolve_cancellation(document_data, paths.git_dir)
    variables = Variables.from_mappings(global_data, document_data)

    expected = safe_load(Path(expected_path).read_text(encoding="utf8"))
    assert convert(variables) == expected


@mark.parametrize(
    "document_path",
    [
        Path("company1/2020-10-001-Company.yml"),
        Path("company2/2020-10-002-Entreprise client.yml"),
        Path("company2/2020-10-003-Entreprise client.yml"),
        Path("tricatel/2020-11-001-Tricatel.yml"),
    ],
)
def test_generate_xml(document_path: Path) -> None:
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    document_data = safe_load(document_path.read_text(encoding="utf8"))
    document_data = resolve_cancellation(document_data, paths.git_dir)
    variables = Variables.from_mappings(global_data, document_data)

    name = document_path.with_suffix("").name

    xml_bytes = build_xml(name, variables, paths.build_dir)

    assert xml_bytes is not None
    assert (paths.build_dir / document_path.with_suffix(".xml").name).exists()
