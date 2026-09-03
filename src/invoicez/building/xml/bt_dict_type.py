# BG-1 entry: invoice note (subject code + text)
from datetime import date
from typing import NotRequired, TypedDict

NoteBT = TypedDict("NoteBT", {"BT-21": NotRequired[str], "BT-22": str})

# BG-3 entry: previous invoice reference
PreviousInvoiceBT = TypedDict(
    "PreviousInvoiceBT", {"BT-25": str, "BT-26": date}, total=False
)

# Seller information
SellerBT = TypedDict(
    "SellerBT",
    {
        "BT-27": str,
        "BT-30": NotRequired[str],
        "BT-30-1": NotRequired[str],
        "BT-31": str,
        "BT-34": NotRequired[str],
        "BT-34-1": NotRequired[str],
        "BT-35": str,
        "BT-37": str,
        "BT-38": str,
        "BT-40": str,
        "BT-42": str,
        "BT-43": str,
        "BT-63": str,
        "BT-81": str,
        "BT-84": str,
        "BT-85": str,
        "BT-86": str,
    },
)

# Buyer information
CompanyBT = TypedDict(
    "CompanyBT",
    {
        "BT-44": str,
        "BT-47": str,
        "BT-48": str,
        "BT-49": NotRequired[str],
        "BT-49-1": NotRequired[str],
        "BT-50": str,
        "BT-52": str,
        "BT-53": str,
        "BT-55": str,
        # Trade Delivery Hack
        "BT-70": str,
        "BT-71": dict[str, str],
        "BT-75": str,
        "BT-77": str,
        "BT-78": str,
        "BT-80": str,
    },
)


# BG-23 entry: TVA breakdown
TVABreakdownBT = TypedDict(
    "TVABreakdownBT",
    {
        "BT-116": str,
        "BT-117": str,
        "BT-118": str,
        "BT-119": str,
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
        "BT-23": str,
        "BT-24": str,
        "BT-57": str,
        "BG-1": list[NoteBT],
        "BG-3": list[PreviousInvoiceBT],
        # Seller
        "BT-27": str,
        "BT-30": str,
        "BT-30-1": str,
        "BT-31": str,
        "BT-34": str,
        "BT-34-1": str,
        "BT-35": str,
        "BT-37": str,
        "BT-38": str,
        "BT-40": str,
        "BT-42": str,
        "BT-43": str,
        # Buyer
        "BT-44": str,
        "BT-47": str,
        "BT-48": str,
        "BT-49": str,
        "BT-49-1": str,
        "BT-50": str,
        "BT-52": str,
        "BT-53": str,
        "BT-55": str,
        # Trade
        "BT-63": str,
        "BT-70": str,
        "BT-71": dict[str, str],
        "BT-75": str,
        "BT-77": str,
        "BT-78": str,
        "BT-80": str,
        # Payment means
        "BT-81": str,
        "BT-84": str,
        "BT-85": str,
        "BT-86": str,
        # Totals
        "BT-106": str,
        "BT-109": str,
        "BT-110": str,
        "BT-110-1": str,
        "BT-112": str,
        "BT-115": str,
        # Groups
        "BG-23": list[TVABreakdownBT],
        "BG-25": list[InvoiceLineBT],
    },
    total=False,
)
