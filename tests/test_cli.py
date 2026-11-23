import re
from datetime import UTC, datetime
from pathlib import Path
from shutil import copytree, which
from subprocess import run
from typing import Any
from xml.etree import ElementTree

from facturx import get_xml_from_pdf
from git import Repo
from pdfminer.high_level import extract_pages, extract_text
from pydantic import ValidationError
from pytest import fail, fixture, mark, raises
from yaml import safe_dump, safe_load
from yaml import safe_load as yaml_safe_load

from invoicez.building.xml.builder import build_xml
from invoicez.building.xml.variables_to_bt_converter import convert
from invoicez.cancellation import resolve_cancellation
from invoicez.exceptions import InvoicezError
from invoicez.paths import Paths
from invoicez.superpdp import recovery
from invoicez.superpdp.invoices import DuplicateInvoiceError, OutcomeUnknownError
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
    assert isinstance(xml_bytes, bytes)
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


def _siren(root: ElementTree.Element, party: str) -> tuple[str | None, str | None]:
    siren = root.find(
        f".//ram:{party}TradeParty/ram:SpecifiedLegalOrganization/ram:ID", NS
    )
    assert siren is not None
    return siren.text, siren.get("schemeID")


@mark.parametrize(
    "document_path,buyer",
    [
        (Path("company1/2020-10-001-Company.yml"), "666666666"),
        (Path("company2/2020-10-003-Entreprise client.yml"), "123456789"),
        (Path("tricatel/2020-11-001-Tricatel.yml"), "000000001"),
    ],
)
def test_xml_has_the_sirens(document_path: Path, buyer: str) -> None:
    # Factur-x 6.8 left out that of the buyer, whose invoices were then rejected.
    paths = Paths()
    global_data = safe_load(paths.variables.read_text(encoding="utf8"))
    document_data = safe_load(document_path.read_text(encoding="utf8"))
    document_data = resolve_cancellation(document_data, paths.git_dir)
    variables = Variables.from_mappings(global_data, document_data)

    root = ElementTree.fromstring(build_xml("sirens", variables, paths.build_dir))
    assert _siren(root, "Seller") == ("000000002", "0002")
    assert _siren(root, "Buyer") == (buyer, "0002")


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


def _event(code: str, text: str = "", *, at: int = 0) -> dict[str, Any]:
    return {
        "id": at,
        "created_at": f"2026-10-14T10:00:{at:02}Z",
        "status_code": code,
        "status_text": text or code,
    }


@fixture
def superpdp(monkeypatch: Any) -> dict[str, Any]:
    """Stand in for SuperPDP, and for the user confirming, when sending.

    Returns:
        The state of the fake: "sent" lists the invoices SuperPDP already has,
        "events" are those of an invoice once sent, "answer" is the reply to the
        confirmation, and "external_ids" lists those of the invoices sent. Sending
        raises "send_error" if set, after which "sent_after" is what SuperPDP has,
        and reading a status raises "status_error" if set. "original" is the file
        SuperPDP was sent, or the error fetching it raises.
    """
    from invoicez import sending

    monkeypatch.setattr(sending, "STATUS_WAIT_SECONDS", 0)
    monkeypatch.setattr(recovery, "RECOVERY_WAIT_SECONDS", 0)
    monkeypatch.setattr(recovery, "RECOVERY_POLL_SECONDS", 0)
    state: dict[str, Any] = {
        "sent": [],
        "events": [_event("fr:201", "Émise par la plateforme")],
        "answer": "y",
        "external_ids": [],
        "send_error": None,
        "sent_after": None,
        "status_error": None,
        "original": None,
    }

    def send_invoice(_: bytes, *, external_id: str) -> dict[str, Any]:
        state["external_ids"].append(external_id)
        if state["send_error"] is not None:
            state["sent"] = state["sent_after"]
            raise state["send_error"]
        return {"id": 42}

    def list_sent_invoices() -> list[dict[str, Any]]:
        if isinstance(state["sent"], Exception):
            raise state["sent"]
        return state["sent"]

    def get_invoice(id_: int) -> dict[str, Any]:
        if state["status_error"] is not None:
            raise state["status_error"]
        return {"id": id_, "events": state["events"]}

    def get_original_invoice(_: int) -> bytes:
        if isinstance(state["original"], Exception):
            raise state["original"]
        return state["original"]

    monkeypatch.setattr(sending, "list_sent_invoices", list_sent_invoices)
    monkeypatch.setattr(sending, "get_original_invoice", get_original_invoice)
    monkeypatch.setattr(
        sending, "validate_invoice", lambda _: {"data": [{"is_valid": True}]}
    )
    monkeypatch.setattr(sending, "send_invoice", send_invoice)
    monkeypatch.setattr(sending, "get_invoice", get_invoice)
    monkeypatch.setattr("builtins.input", lambda _: state["answer"])
    return state


_SENDBOX_PDF = Path("sendbox/2020-10-001-Company.pdf")
_ARCHIVED_PDF = Path("archive/2020/2020-10-001-Company.pdf")


def test_send_archives_the_sent_invoice(
    working_dir: Path, superpdp: dict[str, Any], capsys: Any
) -> None:
    from invoicez.cli.send import send

    send("2020-10-001")

    assert superpdp["external_ids"] == ["2020-10-001"]
    assert not (working_dir / _SENDBOX_PDF).exists()
    assert "Burger Queen" in extract_text(working_dir / _ARCHIVED_PDF)
    output = capsys.readouterr().out
    assert "0225:XXXXXXXXX_XXXXX" in output
    assert "1800.00 EUR incl. VAT" in output
    assert "Émise par la plateforme (fr:201)" in output


def test_send_archives_credit_notes_under_their_year(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli import main
    from invoicez.cli.send import send

    with raises(SystemExit):
        main("cancel", "company1/2020-10-001-Company.yml")
    number = f"{datetime.now():%Y-%m}-001"

    send(number)

    archived = working_dir / "archive" / f"{datetime.now():%Y}"
    assert (archived / f"{number}-Company.pdf").is_file()


def test_send_not_confirmed_leaves_the_invoice_in_sendbox(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["answer"] = "n"
    send("company1/2020-10-001-Company.yml")

    assert superpdp["external_ids"] == []
    assert (working_dir / _SENDBOX_PDF).is_file()
    assert not (working_dir / "archive").exists()


def test_send_yes_does_not_ask(
    working_dir: Path, superpdp: dict[str, Any], monkeypatch: Any
) -> None:
    from invoicez.cli.send import send

    monkeypatch.setattr("builtins.input", lambda _: fail("asked"))
    send("2020-10-001", yes=True)

    assert (working_dir / _ARCHIVED_PDF).is_file()


def test_send_refuses_invoices_already_sent(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["sent"] = [
        {
            "id": 7,
            "en_invoice": {"number": "2020-10-001"},
            "events": [_event("fr:203", "Mise à disposition")],
        }
    ]
    with raises(InvoicezError, match=r"already sent \(SuperPDP id 7, Mise à"):
        send("2020-10-001")

    assert superpdp["external_ids"] == []
    assert not (working_dir / "sendbox").exists()


def test_send_again_invoices_found_invalid(superpdp: dict[str, Any]) -> None:
    from invoicez.cli.send import send

    superpdp["sent"] = [
        {
            "id": 7,
            "en_invoice": {"number": "2020-10-001"},
            "events": [_event("api:invalid")],
        }
    ]
    send("2020-10-001")

    assert superpdp["external_ids"] == ["2020-10-001"]


def test_send_leaves_invoices_found_invalid_in_sendbox(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["events"] = [_event("api:uploaded", at=1), _event("api:invalid", at=2)]
    with raises(InvoicezError, match="found invoice 2020-10-001 invalid"):
        send("2020-10-001")

    assert (working_dir / _SENDBOX_PDF).is_file()
    assert not (working_dir / "archive").exists()


def test_send_reports_problems_after_archiving(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["events"] = [_event("fr:213", "Rejetée")]
    with raises(InvoicezError, match=r"Rejetée \(fr:213\)"):
        send("2020-10-001")

    assert (working_dir / _ARCHIVED_PDF).is_file()


_NUMBER = "2020-10-001"


@mark.parametrize(
    "party,term", [("SellerTradeParty", "BT-30"), ("BuyerTradeParty", "BT-47")]
)
@mark.parametrize(
    "spoiled",
    [
        # Without the group, with another scheme, and not a SIREN.
        (r"<ram:SpecifiedLegalOrganization>.*?</ram:SpecifiedLegalOrganization>", ""),
        (r'(<ram:SpecifiedLegalOrganization>\s*<ram:ID schemeID=)"0002"', r'\1"0009"'),
        (
            r"(<ram:SpecifiedLegalOrganization>\s*<ram:ID[^>]*>)[0-9]+",
            r"\g<1>12345678900011",
        ),
    ],
)
def test_send_refuses_invoices_without_sirens(
    working_dir: Path,
    superpdp: dict[str, Any],
    monkeypatch: Any,
    party: str,
    term: str,
    spoiled: tuple[str, str],
) -> None:
    from invoicez import sending
    from invoicez.cli.send import send

    def get_spoiled_xml(pdf_bytes: bytes) -> tuple[str, bytes]:
        name, xml = get_xml_from_pdf(pdf_bytes)
        assert isinstance(name, str)
        assert isinstance(xml, bytes)
        before, start, rest = xml.decode().partition(f"<ram:{party}>")
        inside, end, after = rest.partition(f"</ram:{party}>")
        inside, count = re.subn(*spoiled, inside, flags=re.DOTALL)
        assert count == 1
        return name, "".join((before, start, inside, end, after)).encode()

    monkeypatch.setattr(sending, "get_xml_from_pdf", get_spoiled_xml)
    with raises(InvoicezError, match=rf"no valid SIREN .*\({term}\)"):
        send(_NUMBER, validate=False)

    assert superpdp["external_ids"] == []
    assert (working_dir / _SENDBOX_PDF).is_file()


def _listed(id_: int, *codes: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "events": [_event(code) for code in codes],
        "en_invoice": {"number": _NUMBER},
        **extra,
    }


@mark.parametrize(
    "found",
    [
        _listed(43, "api:uploaded"),
        # Not read by SuperPDP yet.
        {"id": 43, "external_id": _NUMBER},
    ],
)
def test_send_finds_the_invoice_after_a_timeout(
    working_dir: Path, superpdp: dict[str, Any], capsys: Any, found: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["send_error"] = OutcomeUnknownError("timed out")
    superpdp["sent_after"] = [found]
    send(_NUMBER)

    assert superpdp["external_ids"] == [_NUMBER]
    assert (working_dir / _ARCHIVED_PDF).is_file()
    assert not (working_dir / _SENDBOX_PDF).exists()
    assert "sent with SuperPDP id 43" in capsys.readouterr().out


def test_send_keeps_looking_for_the_invoice_after_a_timeout(
    working_dir: Path, superpdp: dict[str, Any], monkeypatch: Any
) -> None:
    from invoicez import sending
    from invoicez.cli.send import send

    # What listing the invoices gives, before sending then while looking.
    listings = [[], InvoicezError("down"), [], [_listed(43, "api:uploaded")]]

    def list_sent_invoices() -> list[dict[str, Any]]:
        listing = listings.pop(0)
        if isinstance(listing, Exception):
            raise listing
        return listing

    monkeypatch.setattr(recovery, "RECOVERY_WAIT_SECONDS", 60)
    monkeypatch.setattr(sending, "list_sent_invoices", list_sent_invoices)
    superpdp["send_error"] = OutcomeUnknownError("timed out")
    send(_NUMBER)

    assert listings == []
    assert (working_dir / _ARCHIVED_PDF).is_file()


@mark.parametrize(
    "sent_after",
    [
        [],
        # The attempt before, under the same number.
        [_listed(7, "api:invalid")],
        InvoicezError("down"),
    ],
)
def test_send_tells_when_the_outcome_is_unknown(
    working_dir: Path, superpdp: dict[str, Any], sent_after: Any
) -> None:
    from invoicez.cli.send import send

    superpdp["sent"] = [_listed(7, "api:invalid")]
    superpdp["send_error"] = OutcomeUnknownError("timed out")
    superpdp["sent_after"] = sent_after
    with raises(InvoicezError, match=r"timed out\. Invoice 2020-10-001 may have been"):
        send(_NUMBER)

    assert (working_dir / _SENDBOX_PDF).is_file()
    assert not (working_dir / "archive").exists()


def test_send_does_not_look_for_rejected_invoices(
    working_dir: Path, superpdp: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    superpdp["send_error"] = InvoicezError("HTTP 400")
    superpdp["sent_after"] = [_listed(43, "api:uploaded")]
    with raises(InvoicezError, match=r"^HTTP 400$"):
        send(_NUMBER)

    assert (working_dir / _SENDBOX_PDF).is_file()


def test_send_archives_when_the_status_cannot_be_read(
    working_dir: Path, superpdp: dict[str, Any], capsys: Any
) -> None:
    from invoicez.cli.send import send

    superpdp["status_error"] = InvoicezError("timed out")
    send(_NUMBER)

    assert (working_dir / _ARCHIVED_PDF).is_file()
    output = capsys.readouterr().out
    assert "its status could not be read" in output
    assert "sent with SuperPDP id 42" in output


@fixture
def interrupted(working_dir: Path, superpdp: dict[str, Any]) -> dict[str, Any]:
    """Leave in sendbox/ the PDF of an invoice SuperPDP has.

    Returns:
        The state of the fake SuperPDP.
    """
    from invoicez.cli.send import send

    superpdp["answer"] = "n"
    send(_NUMBER)
    superpdp["sent"] = [_listed(7, "fr:203")]
    _, superpdp["original"] = get_xml_from_pdf(
        (working_dir / _SENDBOX_PDF).read_bytes()
    )
    return superpdp


def test_send_archives_what_an_interrupted_sending_left(
    working_dir: Path, interrupted: dict[str, Any], capsys: Any
) -> None:
    from invoicez.cli.send import send

    left = (working_dir / _SENDBOX_PDF).read_bytes()
    send(_NUMBER)

    assert (working_dir / _ARCHIVED_PDF).read_bytes() == left
    assert not (working_dir / _SENDBOX_PDF).exists()
    assert interrupted["external_ids"] == []
    assert "already sent (SuperPDP id 7" in capsys.readouterr().out


def test_send_reports_problems_of_what_an_interrupted_sending_left(
    working_dir: Path, interrupted: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    interrupted["sent"] = [_listed(7, "fr:213")]
    with raises(InvoicezError, match=r"^fr:213 \(fr:213\)$"):
        send(_NUMBER)

    assert (working_dir / _ARCHIVED_PDF).is_file()


@mark.parametrize(
    "original,message",
    [
        (b"<other/>", "as it is not what was sent"),
        (b"%PDF", "as it is not what was sent"),
        (InvoicezError("down"), "there: down"),
    ],
)
def test_send_does_not_archive_what_may_not_have_been_sent(
    working_dir: Path, interrupted: dict[str, Any], original: Any, message: str
) -> None:
    from invoicez.cli.send import send

    interrupted["original"] = original
    with raises(InvoicezError, match=f"already sent .*{message}"):
        send(_NUMBER)

    assert (working_dir / _SENDBOX_PDF).is_file()
    assert not (working_dir / "archive").exists()


@mark.parametrize("damaged", [b"", b"%PDF-1.7 truncated"])
def test_send_does_not_archive_damaged_pdfs(
    working_dir: Path, interrupted: dict[str, Any], damaged: bytes
) -> None:
    from invoicez.cli.send import send

    (working_dir / _SENDBOX_PDF).write_bytes(damaged)
    with raises(InvoicezError, match=r"already sent .* as it is not what was sent"):
        send(_NUMBER)

    assert not (working_dir / "archive").exists()


def test_send_does_not_replace_archived_invoices(
    working_dir: Path, interrupted: dict[str, Any]
) -> None:
    from invoicez.cli.send import send

    archived = working_dir / _ARCHIVED_PDF
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"as sent")
    with raises(InvoicezError, match=r"already sent \(SuperPDP id 7, .*\)$"):
        send(_NUMBER)

    assert archived.read_bytes() == b"as sent"
    assert (working_dir / _SENDBOX_PDF).is_file()


@fixture
def duplicate(interrupted: dict[str, Any]) -> dict[str, Any]:
    """Have SuperPDP refuse an invoice it has, but did not list before.

    Returns:
        The state of the fake SuperPDP.
    """
    interrupted["answer"] = "y"
    interrupted["sent"] = interrupted["sent_after"] = []
    interrupted["send_error"] = DuplicateInvoiceError("déjà existante (id 7)", 7)
    return interrupted


def test_send_archives_invoices_refused_as_already_sent(
    working_dir: Path, duplicate: dict[str, Any], capsys: Any
) -> None:
    from invoicez.cli.send import send

    send(_NUMBER)

    assert duplicate["external_ids"] == [_NUMBER]
    assert (working_dir / _ARCHIVED_PDF).is_file()
    assert not (working_dir / _SENDBOX_PDF).exists()
    output = capsys.readouterr().out
    assert "It is this invoice, sent by an earlier attempt" in output
    assert "sent with SuperPDP id 7: Émise par la plateforme" in output


@mark.parametrize(
    "original,message",
    [
        (b"<other/>", "Its content is not that of this invoice"),
        (InvoicezError("down"), "could not be checked: down"),
    ],
)
def test_send_does_not_archive_invoices_refused_for_another(
    working_dir: Path, duplicate: dict[str, Any], original: Any, message: str
) -> None:
    from invoicez.cli.send import send

    duplicate["original"] = original
    with raises(InvoicezError, match=rf"déjà existante \(id 7\)\. .*{message}$"):
        send(_NUMBER)

    assert (working_dir / _SENDBOX_PDF).is_file()
    assert not (working_dir / "archive").exists()


@mark.parametrize("status_error", [None, InvoicezError("down")])
def test_send_leaves_invoices_refused_as_found_invalid_in_sendbox(
    working_dir: Path, duplicate: dict[str, Any], status_error: Exception | None
) -> None:
    from invoicez.cli.send import send

    # Sent again unchanged, after SuperPDP found it invalid.
    duplicate["sent"] = [_listed(7, "api:invalid")]
    duplicate["events"] = [_event("api:invalid")]
    duplicate["status_error"] = status_error
    with raises(InvoicezError, match="found invoice 2020-10-001 invalid"):
        send(_NUMBER)

    assert (working_dir / _SENDBOX_PDF).is_file()
    assert not (working_dir / "archive").exists()


def _sent(number: str, issue_date: str, *codes: str) -> dict[str, Any]:
    return {
        "id": abs(hash(number)),
        "en_invoice": {"number": number, "issue_date": issue_date},
        "events": [_event(code, at=at) for at, code in enumerate(codes)],
    }


def _rows(output: str) -> list[list[str]]:
    # The cells of each row of the table printed.
    return [
        [cell.strip() for cell in line.split("│")[1:-1]]
        for line in output.splitlines()
        if line.startswith("│")
    ]


def _status(monkeypatch: Any, sent: list[dict[str, Any]], *args: str) -> int:
    from invoicez.cli import main, status

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(status, "list_sent_invoices", lambda: sent)
    with raises(SystemExit) as exit_info:
        main("status", *args)
    return int(exit_info.value.code or 0)


def test_status_shows_invoices_needing_action(monkeypatch: Any, capsys: Any) -> None:
    # 2020-10-003 is a credit note cancelling 2020-10-002, of company2.
    sent = [_sent("2020-10-002", "2020-10-15", "api:uploaded", "fr:203")]
    assert _status(monkeypatch, sent) == 0

    output = capsys.readouterr().out
    rows = _rows(output)
    assert ["not sent", "2020-10-003", "2020-10-18", "company2"] in [
        r[:4] for r in rows
    ]
    assert ["not sent", "BURGERQUEEN-TRICATEL-0001"] in [r[:2] for r in rows]
    # Older than the first invoice SuperPDP has, so sent before it.
    assert "2020-10-001" not in output
    # Cancelled by 2020-10-003.
    assert "2020-10-002" not in output
    assert "2 other invoices" in output

    assert _status(monkeypatch, sent, "--all") == 0
    rows = [row[:2] for row in _rows(capsys.readouterr().out)]
    assert ["not sent", "2020-10-001"] in rows
    assert ["cancelled", "2020-10-002"] in rows


def test_status_shows_invoices_awaiting_payment(monkeypatch: Any, capsys: Any) -> None:
    sent = [
        _sent("2020-10-001", "2020-10-07", "fr:203"),
        _sent("BURGERQUEEN-TRICATEL-0001", "2026-04-12", "fr:203", "fr:212"),
    ]
    assert _status(monkeypatch, sent) == 0

    rows = [row[:2] for row in _rows(capsys.readouterr().out)]
    assert rows == [["not sent", "2020-10-003"], ["awaiting payment", "2020-10-001"]]


def test_status_flags_invoices_not_delivered(monkeypatch: Any, capsys: Any) -> None:
    from invoicez.cli import status

    sent = [_sent("2020-10-001", "2020-10-07", "api:uploaded", "fr:200", "fr:201")]
    monkeypatch.setattr(status, "_now", lambda: datetime(2026, 10, 15, tzinfo=UTC))
    assert _status(monkeypatch, sent) == 0
    rows = {row[1]: row for row in _rows(capsys.readouterr().out)}
    assert rows["2020-10-001"][0] == "awaiting payment"

    monkeypatch.setattr(status, "_now", lambda: datetime(2026, 10, 20, tzinfo=UTC))
    assert _status(monkeypatch, sent) == 0
    rows = _rows(capsys.readouterr().out)
    # Shown right after problems.
    assert rows[0][:2] == ["not delivered?", "2020-10-001"]
    assert rows[0][4] == "fr:201 (fr:201) since 2026-10-14"


def test_status_fails_on_problems(monkeypatch: Any, capsys: Any) -> None:
    sent = [_sent("2020-10-001", "2020-10-07", "fr:203", "fr:210")]
    assert _status(monkeypatch, sent) == 1
    rows = _rows(capsys.readouterr().out)
    assert rows[0][:2] == ["problem", "2020-10-001"]
    assert rows[0][4] == "fr:210 (fr:210)"


def _rejected(external_id: str | None) -> dict[str, Any]:
    # SuperPDP gives no number for these.
    return {
        "id": 99,
        "external_id": external_id,
        "en_invoice": None,
        "events": [_event("api:uploaded"), _event("fr:213", at=1)],
    }


def test_status_shows_rejected_invoices_as_problems(
    monkeypatch: Any, capsys: Any
) -> None:
    sent = [_sent("2020-10-001", "2020-10-07", "fr:203"), _rejected("2020-10-003")]
    assert _status(monkeypatch, sent) == 1
    rows = _rows(capsys.readouterr().out)
    assert rows[0][:4] == ["problem", "2020-10-003", "2020-10-18", "company2"]


def test_status_hides_rejected_invoices_without_a_document(
    monkeypatch: Any, capsys: Any
) -> None:
    sent = [_rejected("2020-10-099"), _rejected(None)]
    assert _status(monkeypatch, sent) == 0
    output = capsys.readouterr().out
    assert "2020-10-099" not in output
    # Nothing tells when the first invoice was sent.
    assert ["not sent", "2020-10-001"] in [row[:2] for row in _rows(output)]

    assert _status(monkeypatch, sent, "--all") == 1
    assert ["problem", "2020-10-099", ""] in [
        row[:3] for row in _rows(capsys.readouterr().out)
    ]


def test_status_prefers_the_invoice_sent_again(monkeypatch: Any, capsys: Any) -> None:
    sent = [_rejected("2020-10-001"), _sent("2020-10-001", "2020-10-07", "fr:203")]
    assert _status(monkeypatch, sent) == 0
    rows = [row[:2] for row in _rows(capsys.readouterr().out)]
    assert ["awaiting payment", "2020-10-001"] in rows


def test_status_ignores_problems_of_invoices_cancelled_by_credit_note(
    monkeypatch: Any, capsys: Any
) -> None:
    sent = [_sent("2020-10-002", "2020-10-15", "fr:203", "fr:210")]
    assert _status(monkeypatch, sent) == 0
    assert "2020-10-002" not in capsys.readouterr().out


@fixture
def paying(monkeypatch: Any) -> dict[str, Any]:
    """Stand in for SuperPDP, and for the user confirming, when reporting payments.

    Returns:
        The state of the fake: "sent" lists the invoices SuperPDP has, "answer"
        is the reply to the confirmation, and "paid" lists the SuperPDP ids of
        the invoices reported as paid. Reporting raises "error" if set, after which
        "events" are those of the invoice, or the error reading them raises.
    """
    from invoicez.cli import paid

    state: dict[str, Any] = {
        "sent": [],
        "answer": "y",
        "paid": [],
        "error": None,
        "events": [],
    }

    def mark_paid(invoice_id: int) -> dict[str, Any]:
        state["paid"].append(invoice_id)
        if state["error"] is not None:
            raise state["error"]
        return _event("fr:212", "Encaissée")

    def get_invoice(id_: int) -> dict[str, Any]:
        if isinstance(state["events"], Exception):
            raise state["events"]
        return {"id": id_, "events": state["events"]}

    monkeypatch.setattr(recovery, "RECOVERY_WAIT_SECONDS", 0)
    monkeypatch.setattr(recovery, "RECOVERY_POLL_SECONDS", 0)
    monkeypatch.setattr(paid, "get_invoice", get_invoice)
    monkeypatch.setattr(paid, "list_sent_invoices", lambda: state["sent"])
    monkeypatch.setattr(paid, "mark_paid", mark_paid)
    monkeypatch.setattr("builtins.input", lambda _: state["answer"])
    return state


def _sent_with_id(id_: int, number: str, *codes: str) -> dict[str, Any]:
    return {**_sent(number, "2020-10-07", *codes), "id": id_}


def test_paid_reports_invoices_once_confirmed(
    working_dir: Path, paying: dict[str, Any], capsys: Any
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [
        _sent_with_id(7, "2020-10-001", "fr:203"),
        _sent_with_id(8, "2020-10-002", "fr:203", "fr:212"),
    ]
    paid("2020-10-001", "company1/2020-10-001-Company.yml", "2020-10-002")

    # Given twice, reported once; already paid, skipped.
    assert paying["paid"] == [7]
    output = capsys.readouterr().out
    assert "2020-10-002 is already paid, skipped" in output
    assert "2020-10-001: Encaissée (fr:212)" in output


def test_paid_not_confirmed_reports_nothing(
    working_dir: Path, paying: dict[str, Any]
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [_sent_with_id(7, "2020-10-001", "fr:203")]
    paying["answer"] = "n"
    paid("2020-10-001")

    assert paying["paid"] == []


def test_paid_yes_does_not_ask(
    working_dir: Path, paying: dict[str, Any], monkeypatch: Any
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [_sent_with_id(7, "2020-10-001", "fr:203")]
    monkeypatch.setattr("builtins.input", lambda _: fail("asked"))
    paid("2020-10-001", yes=True)

    assert paying["paid"] == [7]


@mark.parametrize(
    ("codes", "number", "message"),
    [
        ((), "2020-10-001", "2020-10-001 was not sent through SuperPDP"),
        (("api:invalid",), "2020-10-001", "2020-10-001 was not sent through"),
        (("fr:203", "fr:220"), "2020-10-001", "2020-10-001 is cancelled on SuperPDP"),
        (("fr:213",), "2020-10-001", "2020-10-001 was rejected"),
        (("fr:203",), "2020-10-003", "2020-10-003 is a credit note"),
    ],
)
def test_paid_checks_every_invoice_before_reporting(
    working_dir: Path,
    paying: dict[str, Any],
    codes: tuple[str, ...],
    number: str,
    message: str,
) -> None:
    from invoicez.cli.paid import paid

    # A valid invoice given first is not reported either.
    paying["sent"] = [_sent_with_id(8, "2020-10-002", "fr:203")]
    if codes:
        paying["sent"].append(_sent_with_id(7, number, *codes))
    with raises(InvoicezError, match=message):
        paid("2020-10-002", number)

    assert paying["paid"] == []


def test_paid_refuses_rejected_invoices_without_a_number(
    working_dir: Path, paying: dict[str, Any]
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [_rejected("2020-10-001")]
    with raises(InvoicezError, match="2020-10-001 was rejected"):
        paid("2020-10-001")

    assert paying["paid"] == []


def test_paid_finds_the_payment_after_a_timeout(
    working_dir: Path, paying: dict[str, Any], capsys: Any
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [_sent_with_id(7, "2020-10-001", "fr:203")]
    paying["error"] = OutcomeUnknownError("timed out")
    paying["events"] = [_event("fr:203"), _event("fr:212", "Encaissée", at=1)]
    paid("2020-10-001")

    assert paying["paid"] == [7]
    assert "2020-10-001: Encaissée (fr:212)" in capsys.readouterr().out


@mark.parametrize("events", [[_event("fr:203")], InvoicezError("down")])
def test_paid_tells_when_the_outcome_is_unknown(
    working_dir: Path, paying: dict[str, Any], events: Any
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [
        _sent_with_id(7, "2020-10-001", "fr:203"),
        _sent_with_id(8, "2020-10-002", "fr:203"),
    ]
    paying["error"] = OutcomeUnknownError("timed out")
    paying["events"] = events
    with raises(InvoicezError, match=r"timed out\. 2020-10-001 may be reported"):
        paid("2020-10-001", "2020-10-002")

    assert paying["paid"] == [7]


def test_paid_does_not_look_for_rejected_payments(
    working_dir: Path, paying: dict[str, Any]
) -> None:
    from invoicez.cli.paid import paid

    paying["sent"] = [_sent_with_id(7, "2020-10-001", "fr:203")]
    paying["error"] = InvoicezError("HTTP 400")
    paying["events"] = [_event("fr:212")]
    with raises(InvoicezError, match=r"^HTTP 400$"):
        paid("2020-10-001")


def test_entrypoint_shows_errors_without_traceback(
    monkeypatch: Any, capsys: Any
) -> None:
    from invoicez.cli import entrypoint

    monkeypatch.setattr("sys.argv", ["invoicez", "send", "[nope]"])
    with raises(SystemExit) as exit_:
        entrypoint()

    assert exit_.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: '[nope]' is neither a file nor a document number.\n"


def test_complete_documents(working_dir: Path, capsys: Any) -> None:
    from invoicez.cli import main

    (working_dir / "quote.yml").write_text(
        "document_number: 2020-10-009\nquote: true\n", encoding="utf8"
    )
    with raises(SystemExit):
        main("cancel", "2020-10-001")
    with raises(SystemExit):
        main("complete-documents")

    lines = capsys.readouterr().out.splitlines()
    assert "2020-10-001:company1 2020-10-07" in lines
    assert f"{datetime.now():%Y-%m}-001:credit note for 2020-10-001 " in "\n".join(
        lines
    )
    assert not any(line.startswith("2020-10-009") for line in lines)


def test_zsh_completion_offers_documents() -> None:
    from invoicez.cli import app

    script = app.generate_completion(shell="zsh", prog_name="invoicez")

    assert script.startswith("#compdef invoicez\n\n_invoicez_documents() {")
    assert script.count(":_invoicez_documents'") == 7
    assert "complete-documents" not in script.split("_invoicez_documents() {")[2:]
    if zsh := which("zsh"):
        check = run([zsh, "-n"], input=script, text=True, check=False)
        assert check.returncode == 0
