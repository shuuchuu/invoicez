# The data dict factur-x's generate_xml takes: header fields keyed by their
# EN16931 business term (BT-n), groups (BG-n) as lists of such dicts, and
# parties as dicts keyed by factur-x's own field names.
from datetime import date
from typing import NotRequired, TypedDict

# BG-1 entry: invoice note (subject code + text)
NoteBT = TypedDict("NoteBT", {"BT-21": NotRequired[str], "BT-22": str})

# BG-3 entry: previous invoice reference
PreviousInvoiceBT = TypedDict(
    "PreviousInvoiceBT", {"BT-25": str, "BT-26": date}, total=False
)


class ContactBT(TypedDict, total=False):
    phone: str
    email: str


# Seller (BG-4), buyer (BG-7) or delivery (BG-13) party
class PartyBT(TypedDict, total=False):
    name: str
    # Scheme ID to identifier
    identifiers: dict[str, str]
    legal_identifier: str
    legal_identifier_schemeid: str
    vat_identifier: str
    einvoicing_addr: str
    einvoicing_addr_schemeid: str
    contacts: list[ContactBT]
    addr_l1: str
    city: str
    postcode: str
    country_code: str


# BG-23 entry: TVA breakdown
TVABreakdownBT = TypedDict(
    "TVABreakdownBT",
    {
        "BT-116": str,
        "BT-117": str,
        "BT-118": str,
        "BT-119": str,
        "BT-121": str,
    },
    total=False,
)

# BG-25 entry: invoice line
InvoiceLineBT = TypedDict(
    "InvoiceLineBT",
    {
        "BT-126": str,
        "BT-129": str,
        "BT-130": str,
        "BT-131": str,
        "BT-134": NotRequired[date],
        "BT-135": NotRequired[date],
        "BT-146": str,
        "BT-148": str,
        "BT-151": str,
        "BT-152": str,
        "BT-153": str,
    },
)

# Full invoice in BT format, ready to be passed to facturx
BT = TypedDict(
    "BT",
    {
        # Invoice meta
        "BT-1": str,
        "BT-2": date,
        "BT-3": str,
        "BT-5": str,
        "BT-9": date,
        "BT-12": str,
        "BT-13": str,
        "BT-23": str,
        "BT-24": str,
        "BG-1": list[NoteBT],
        "BG-3": list[PreviousInvoiceBT],
        # Parties
        "BG-4": PartyBT,
        "BG-7": PartyBT,
        "BG-13": PartyBT,
        # Payment means
        "BT-81": str,
        "BT-84": str,
        "BT-85": str,
        "BT-86": str,
        # Totals
        "BT-106": str,
        "BT-109": str,
        "BT-110": str,
        "BT-112": str,
        "BT-115": str,
        # Groups
        "BG-23": list[TVABreakdownBT],
        "BG-25": list[InvoiceLineBT],
    },
    total=False,
)
