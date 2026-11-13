from collections.abc import Iterable
from datetime import date

from invoicez.building.xml.bt_dict_type import (
    BT,
    InvoiceLineBT,
    NoteBT,
    PartyBT,
    PreviousInvoiceBT,
    TVABreakdownBT,
)
from invoicez.variables import (
    TVA,
    Company,
    InvoiceLine,
    InvoiceMeta,
    Notices,
    Seller,
    Variables,
    compute_vat_id,
)

# Exemption reason for training services, article 261-4-4° du CGI.
_TVA_EXEMPTION_CODE = "VATEX-FR-CGI261-4"


def _convert_tva(tva: TVA) -> TVABreakdownBT:
    d: TVABreakdownBT = {
        "BT-118": tva.category,
        "BT-119": f"{tva.rate * 100:.2f}",
    }
    if tva.category == "E":
        d["BT-121"] = _TVA_EXEMPTION_CODE
    return d


def _convert_seller(seller: Seller) -> PartyBT:
    return {
        "name": seller.name,
        "identifiers": {"0009": seller.siret},  # SIRET-CODE scheme
        "legal_identifier": seller.siren,
        "legal_identifier_schemeid": "0002",  # SIRENE scheme
        "vat_identifier": seller.vat_id,
        "einvoicing_addr": seller.electronic_address,
        "einvoicing_addr_schemeid": seller.electronic_address_scheme,
        "contacts": [{"phone": seller.phone, "email": seller.email}],
        "addr_l1": seller.address,
        "city": seller.city,
        "postcode": seller.postcode,
        "country_code": seller.country,
    }


def _convert_buyer(company: Company) -> PartyBT:
    return {
        "name": company.name,
        "legal_identifier": company.siren,
        "legal_identifier_schemeid": "0002",  # SIRENE scheme
        "vat_identifier": compute_vat_id(company.siren),
        "einvoicing_addr": company.electronic_address,
        "einvoicing_addr_schemeid": company.electronic_address_scheme,
        "addr_l1": company.address,
        "city": company.city,
        "postcode": company.postcode,
        "country_code": company.country,
    }


def _convert_delivery(company: Company) -> PartyBT:
    # ApplicableHeaderTradeDelivery is mandatory in CII but not in en16931: the
    # check fails without it, so the buyer stands for the delivery party.
    return {
        "identifiers": {"0002": company.siren},  # SIRENE scheme
        "name": company.name,
        "addr_l1": company.address,
        "city": company.city,
        "postcode": company.postcode,
        "country_code": company.country,
    }


def _convert_notices(notices: Notices) -> list[NoteBT]:
    conversion_fields = {
        "recouvrement": "PMT",
        "taux_retard": "PMD",
        "escompte": "AAB",
        "legal": "ABL",
        "general": "AAI",
    }
    return [
        {"BT-21": conversion_fields[k], "BT-22": v}
        for k, v in notices.model_dump().items()
        if v
    ]


def _convert_invoice_meta(invoice_meta: InvoiceMeta) -> BT:
    d: BT = {
        "BT-3": invoice_meta.type_code,
        "BT-5": invoice_meta.currency,
        "BT-23": invoice_meta.type_invoice,
        "BT-24": invoice_meta.spec_format,
    }

    return d


def _add_invoice_infos(
    d: BT,
    document_number: str,
    date: date,
    description: str | None,
    previous_document_number: str | None,
    previous_document_date: date | None,
    invoice_meta: InvoiceMeta,
    due_date: date,
) -> None:
    d.update(_convert_invoice_meta(invoice_meta))
    d["BT-1"] = document_number
    d["BT-2"] = date
    previous_invoice: PreviousInvoiceBT = {}
    if previous_document_number:
        previous_invoice["BT-25"] = previous_document_number
    if previous_document_date:
        previous_invoice["BT-26"] = previous_document_date

    if previous_invoice:
        d["BG-3"] = [previous_invoice]
    d["BT-9"] = due_date
    if description:
        d.setdefault("BG-1", []).append({"BT-22": description})


def _add_notices(d: BT, notices: Notices) -> None:
    d.setdefault("BG-1", []).extend(_convert_notices(notices))


def _add_reference(d: BT, reference: str | None, company: Company) -> None:
    if not reference:
        return
    if company.reference_kind == "contract":
        d["BT-12"] = reference
    else:
        d["BT-13"] = reference


def _add_seller(d: BT, seller: Seller) -> None:
    d["BG-4"] = _convert_seller(seller)
    d["BT-81"] = seller.means_code
    d["BT-84"] = seller.iban
    d["BT-85"] = seller.account_name
    d["BT-86"] = seller.bic


def _add_buyer(d: BT, company: Company) -> None:
    d["BG-7"] = _convert_buyer(company)
    d["BG-13"] = _convert_delivery(company)


def _add_invoice_line(
    invoice_line: InvoiceLine, lines: list[InvoiceLineBT], tva: TVA
) -> float:
    line_number = len(lines) + 1
    line: InvoiceLineBT = {
        "BT-126": f"{line_number}",
        "BT-153": invoice_line.description,
        "BT-148": f"{invoice_line.pu}",
        "BT-146": f"{invoice_line.pu:.2f}",
        "BT-129": f"{invoice_line.n:.4f}",
        "BT-151": tva.category,
        "BT-152": f"{tva.rate * 100:.2f}",
        "BT-130": invoice_line.unit,
        "BT-131": f"{invoice_line.pu * invoice_line.n:.2f}",
    }
    if invoice_line.start:
        line["BT-134"] = invoice_line.start
    if invoice_line.end:
        line["BT-135"] = invoice_line.end
    lines.append(line)
    return invoice_line.pu * invoice_line.n


def _add_products(d: BT, products: Iterable[InvoiceLine], tva: TVA) -> None:
    lines: list[InvoiceLineBT] = []
    line_total = 0.0
    for invoice_line in products:
        line_total += _add_invoice_line(invoice_line, lines, tva)
    line_tva = line_total * tva.rate
    line_ttc = line_total + line_tva

    # Lignes de facturations
    d["BG-25"] = lines
    d["BT-106"] = f"{line_total:.2f}"  # HT
    d["BT-109"] = f"{line_total:.2f}"  # HT
    d["BT-110"] = f"{line_tva:.2f}"  # TVA
    d["BT-112"] = f"{line_ttc:.2f}"  # TTC
    d["BT-115"] = f"{line_ttc:.2f}"  # TTC

    # Ventilation TVA
    ventilation_tva = _convert_tva(tva)
    ventilation_tva["BT-116"] = f"{line_total:.2f}"
    ventilation_tva["BT-117"] = f"{line_tva:.2f}"
    d["BG-23"] = [ventilation_tva]


def convert(variables: Variables) -> BT:
    d: BT = {}
    _add_invoice_infos(
        d,
        variables.document_number,
        variables.date,
        variables.description,
        variables.previous_document_number,
        variables.previous_document_date,
        variables.invoice_meta,
        variables.due_date,
    )
    _add_notices(d, variables.notices)
    _add_reference(d, variables.reference, variables.company)
    _add_seller(d, variables.user)
    _add_buyer(d, variables.company)
    _add_products(d, variables.products, variables.tva)
    return d
