from collections.abc import Iterable
from datetime import date

from invoicez.building.xml.bt_dict_type import (
    BT,
    CompanyBT,
    InvoiceLineBT,
    NoteBT,
    PreviousInvoiceBT,
    SellerBT,
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


def _compute_vat_id(siren: str) -> str:
    vat_key = (12 + 3 * (int(siren) % 97)) % 97
    return f"FR{vat_key:02}{siren}"


def _convert_seller(seller: Seller) -> SellerBT:
    vat_id = _compute_vat_id(seller.siren)
    return {
        "BT-27": seller.name,
        "BT-29": {"0009": seller.siret},  # SIRET-CODE scheme
        "BT-30": seller.siren,
        "BT-30-1": "0002",  # SIRENE scheme
        "BT-34": seller.electronic_address,
        "BT-34-1": seller.electronic_address_scheme,
        "BT-40": seller.country,
        "BT-35": seller.address,
        "BT-37": seller.city,
        "BT-38": seller.postcode,
        "BT-31": vat_id,
        "BT-43": seller.email,
        "BT-42": seller.phone,
        "BT-63": vat_id,
        "BT-81": seller.means_code,
        "BT-84": seller.iban,
        "BT-85": seller.account_name,
        "BT-86": seller.bic,
    }


def _convert_company(company: Company) -> CompanyBT:
    vat_id = _compute_vat_id(company.siren)
    d: CompanyBT = {
        "BT-44": company.name,
        "BT-50": company.address,
        "BT-52": company.city,
        "BT-53": company.postcode,
        "BT-55": company.country,
        "BT-47": company.siren,
        "BT-48": vat_id,
        # TRADE MOCK
        # ApplicableHeaderTradeDelivery is mandatory in the definition of CII
        # but not in en16931. Check fail if not indicated
        "BT-71": {
            "0002": company.siren,  # SIRENE scheme, takes the SIREN
            #  directly
        },
        "BT-70": company.name,
        "BT-80": company.country,
        "BT-78": company.postcode,
        "BT-77": company.city,
        "BT-75": company.address,
    }
    if company.electronic_address is not None:
        d["BT-49"] = company.electronic_address
    if company.electronic_address_scheme is not None:
        d["BT-49-1"] = company.electronic_address_scheme
    return d


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
    d.update(_convert_seller(seller))


def _add_buyer(d: BT, company: Company) -> None:
    d.update(_convert_company(company))


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


def _add_products(
    d: BT, products: Iterable[InvoiceLine], tva: TVA, currency: str
) -> None:
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
    d["BT-110-1"] = currency  # Currency
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
    _add_products(d, variables.products, variables.tva, variables.invoice_meta.currency)
    return d
