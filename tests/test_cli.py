from datetime import datetime
from pathlib import Path
from shutil import copytree
from typing import Any
from xml.etree import ElementTree

from facturx import get_xml_from_pdf
from git import Repo
from pdfminer.high_level import extract_pages, extract_text
from pydantic import ValidationError
from pytest import fixture, mark, raises
from yaml import safe_dump, safe_load
from yaml import safe_load as yaml_safe_load

from invoicez.building.xml.builder import build_xml
from invoicez.building.xml.variables_to_bt_converter import convert
from invoicez.cancellation import resolve_cancellation
from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.variables import Variables

DATA_DIR = Path(__file__).parent / __name__

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": (
        "urn:un:unece:uncefact:data:standard:"
        "ReusableAggregateBusinessInformationEntity:100"
    ),
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
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

    pdf_path = (paths.sendbox_dir / document_path.name).with_suffix(".pdf")
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

    pdf_path = working_dir / "sendbox" / path_cancel.with_suffix(".pdf").name
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


def test_cancel_by_number(working_dir: Path) -> None:
    from invoicez.cli import main

    with raises(SystemExit):
        main("cancel", "2020-10-002")

    path_cancel = working_dir / f"{datetime.now():%Y-%m}-001-Entreprise client.yml"
    content = yaml_safe_load(path_cancel.read_text(encoding="utf8"))
    assert content["cancels"] == "2020-10-002"


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


def _xml_due_date(document_data: dict[str, Any]) -> str | None:
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    document_data = resolve_cancellation(document_data, paths.git_dir)
    variables = Variables.from_mappings(global_data, document_data)
    root = ElementTree.fromstring(build_xml("due-date", variables, paths.build_dir))
    due_date = root.find(
        ".//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString",
        NS,
    )
    assert due_date is not None
    return due_date.text


@mark.parametrize(
    "payment_due_date,expected",
    [(None, "20201114"), ("2020-12-31", "20201231")],
)
def test_payment_due_date(payment_due_date: str | None, expected: str) -> None:
    document_path = Path("company2/2020-10-002-Entreprise client.yml")
    document_data = safe_load(document_path.read_text(encoding="utf8"))
    if payment_due_date is not None:
        document_data["payment_due_date"] = payment_due_date

    assert _xml_due_date(document_data) == expected


def test_payment_due_date_not_inherited_by_credit_note() -> None:
    document_path = Path("company2/2020-10-002-Entreprise client.yml")
    document_data = safe_load(document_path.read_text(encoding="utf8"))
    document_data["payment_due_date"] = "2020-12-31"
    document_path.write_text(safe_dump(document_data), encoding="utf8")

    credit_note = {
        "document_number": "2021-01-001",
        "date": "2021-01-10",
        "cancels": "2020-10-002",
    }

    # 2021-01-10 + company2's maximum_days (30).
    assert _xml_due_date(credit_note) == "20210209"


def test_source_does_not_change_the_xml() -> None:
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    document_path = Path("company2/2020-10-002-Entreprise client.yml")
    document_data = safe_load(document_path.read_text(encoding="utf8"))
    source = {
        "kind": "gcal",
        "calendar_id": "calendar@group.calendar.google.com",
        "event_ids": ["event1", "event2"],
        "fingerprint": "0" * 64,
    }

    without_source = Variables.from_mappings(global_data, document_data)
    with_source = Variables.from_mappings(
        global_data, {**document_data, "source": source}
    )

    assert with_source.source is not None
    assert with_source.source.event_ids == ["event1", "event2"]
    assert build_xml("a", with_source, paths.build_dir) == build_xml(
        "b", without_source, paths.build_dir
    )


def test_source_rejects_unknown_kind() -> None:
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    document_path = Path("company2/2020-10-002-Entreprise client.yml")
    document_data = safe_load(document_path.read_text(encoding="utf8"))
    source = {"kind": "other", "calendar_id": "c", "event_ids": [], "fingerprint": ""}

    with raises(ValidationError):
        Variables.from_mappings(global_data, {**document_data, "source": source})


def test_run_by_number_builds_in_root_sendbox(
    working_dir: Path, monkeypatch: Any
) -> None:
    from invoicez.cli import main

    monkeypatch.chdir(working_dir / "company2")
    with raises(SystemExit):
        main("run", "2020-10-001")

    assert (working_dir / "sendbox" / "2020-10-001-Company.pdf").is_file()
    assert not (working_dir / "company2" / "sendbox").exists()


def test_resolve_document_rejects_non_yaml_files(working_dir: Path) -> None:
    pdf_path = working_dir / "invoice.pdf"
    pdf_path.write_bytes(b"%PDF")
    with raises(InvoicezError, match="not a YAML document"):
        Paths().resolve_document("invoice.pdf")
    with raises(InvoicezError, match="Could not find an invoice"):
        Paths().resolve_document("1999-01-001")


@fixture
def superpdp(monkeypatch: Any) -> dict[str, Any]:
    """Stand in for SuperPDP in the send command.

    Returns:
        The state of the fake: set "sent" to the invoice SuperPDP already has, and
        read the external ids of the invoices sent in "external_ids".
    """
    from invoicez.cli import send

    state: dict[str, Any] = {"sent": None, "external_ids": []}

    def send_invoice(_: bytes, *, external_id: str) -> dict[str, Any]:
        state["external_ids"].append(external_id)
        return {"id": 42}

    monkeypatch.setattr(send, "find_sent_invoice", lambda _: state["sent"])
    monkeypatch.setattr(
        send, "validate_invoice", lambda _: {"data": [{"is_valid": True}]}
    )
    monkeypatch.setattr(send, "send_invoice", send_invoice)
    return state


def test_send_archives_the_sent_invoice(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    send("2020-10-001", dry_run=False)

    assert superpdp["external_ids"] == ["2020-10-001"]
    assert not (working_dir / "sendbox" / "2020-10-001-Company.pdf").exists()
    archived = working_dir / "archive" / "company1" / "2020" / "2020-10-001-Company.pdf"
    assert "Burger Queen" in extract_text(archived)


def test_send_archives_credit_notes_under_the_cancelled_company(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli import main
    from invoicez.cli.send import send

    with raises(SystemExit):
        main("cancel", "company1/2020-10-001-Company.yml")
    number = f"{datetime.now():%Y-%m}-001"

    send(number, dry_run=False)

    archived = working_dir / "archive" / "company1" / f"{datetime.now():%Y}"
    assert (archived / f"{number}-Company.pdf").is_file()


def test_send_dry_run_leaves_the_invoice_in_sendbox(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    send("company1/2020-10-001-Company.yml")

    assert superpdp["external_ids"] == []
    assert (working_dir / "sendbox" / "2020-10-001-Company.pdf").is_file()
    assert not (working_dir / "archive").exists()


def test_send_refuses_invoices_already_sent(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["sent"] = {"id": 7}
    with raises(InvoicezError, match="already sent"):
        send("2020-10-001", dry_run=False)

    assert superpdp["external_ids"] == []
    assert not (working_dir / "sendbox").exists()
