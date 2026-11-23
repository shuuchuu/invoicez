r"""Probe how the SuperPDP sandbox treats duplicates and client timeouts.

Sends test invoices between sandbox companies (Tricatel and Burger Queen only), and
refuses to run on a production account. The findings are in
docs/superpdp-duplicates.md. Run it with the credentials of a sandbox company, kept
apart from yours, in ~/.config/invoicez-sandbox/invoicez/superpdp-creds.yml (the
cache is apart too, not to reuse a production token):

    XDG_CONFIG_HOME=~/.config/invoicez-sandbox \
    XDG_CACHE_HOME=~/.cache/invoicez-sandbox \
        uv run python scripts/probe_superpdp_duplicates.py
"""

import logging
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from xml.etree import ElementTree

import niquests

from invoicez.superpdp import invoices as api


def _require_sandbox() -> None:
    company = api._request("GET", "/companies/me", "Company")  # ruff: ignore[private-member-access]
    if company.get("env") != "sandbox":
        sys.exit(f"Refused: env is {company.get('env')!r}, not 'sandbox'.")
    print("Sandbox company:", company["formal_name"])


def _new_invoice() -> bytes:
    # The buyer is picked at random among every sandbox company.
    for _ in range(40):
        response = api._call(  # ruff: ignore[private-member-access]
            "GET", "/invoices/generate_test_invoice", params={"format": "cii"}
        )
        response.raise_for_status()
        xml = response.content or b""
        text = xml.decode().casefold()
        if "tricatel" in text and "burger queen" in text:
            return xml
    sys.exit("Refused: no invoice between Tricatel and Burger Queen was generated.")


def _number(xml: bytes) -> bytes:
    root = ElementTree.fromstring(xml)
    document = next(e for e in root if e.tag.endswith("}ExchangedDocument"))
    number = next(e for e in document if e.tag.endswith("}ID")).text
    assert number is not None
    return number.encode()


def _sub(pattern: bytes, replacement: bytes, xml: bytes) -> bytes:
    changed, count = re.subn(pattern, replacement, xml, count=1, flags=re.DOTALL)
    assert count == 1, pattern
    return changed


def _post(path: str, *, timeout: float = 120, delay: float = 0, **kwargs: Any) -> str:
    time.sleep(delay)
    start = time.monotonic()
    try:
        response = api._call(  # ruff: ignore[private-member-access]
            "POST", path, timeout=timeout, **kwargs
        )
    except niquests.RequestException as error:
        return f"{type(error).__name__} in {time.monotonic() - start:.1f}s"
    body = response.json()
    return (
        f"HTTP {response.status_code} in {time.monotonic() - start:.1f}s: "
        f"{body.get('id') or body.get('message')}"
    )


def _send(xml: bytes, external_id: str, **kwargs: Any) -> str:
    return _post("/invoices", params={"external_id": external_id}, data=xml, **kwargs)


def _find(external_id: str) -> list[dict]:
    return [i for i in api.list_sent_invoices() if i.get("external_id") == external_id]


def _codes(invoice: dict) -> list[str]:
    return [event["status_code"] for event in invoice.get("events") or []]


def main() -> None:
    logging.disable(logging.INFO)
    _require_sandbox()
    run = uuid.uuid4().hex[:8]

    print("\n# Sent again")
    first, other = _new_invoice(), _new_invoice()
    print("first:", _send(first, f"{run}-a"))
    print("same XML, same external_id:", _send(first, f"{run}-a"))
    print("same XML, other external_id:", _send(first, f"{run}-b"))
    renumbered = _sub(
        rb"(<SpecifiedTradeProduct.*?<Name[^>]*>)[^<]+",
        rb"\1Changed",
        other.replace(_number(other), _number(first)),
    )
    print("same number, other content:", _send(renumbered, f"{run}-b"))
    print("other number, same external_id:", _send(other, f"{run}-a"))

    print("\n# Overlapping")
    for delay in (0, 0.1, 0.3):
        xml = _new_invoice()
        with ThreadPoolExecutor() as pool:
            sent = [pool.submit(_send, xml, f"{run}-c", delay=d) for d in (0, delay)]
            print(f"+{delay}s:", [future.result() for future in sent])

    print("\n# Client timeout")
    start = time.monotonic()
    print(_send(_new_invoice(), f"{run}-d", timeout=0.2))
    while not (found := _find(f"{run}-d")) and time.monotonic() - start < 90:
        time.sleep(1)
    print(f"listed: {bool(found)}, {time.monotonic() - start:.1f}s after sending it")

    print("\n# Sent again after api:invalid")
    valid = _new_invoice()
    invalid = _sub(rb"(<GrandTotalAmount[^>]*>)[^<]+", rb"\g<1>1.00", valid)
    print("wrong total:", _send(invalid, f"{run}-e"))
    time.sleep(10)
    print("events:", [_codes(invoice) for invoice in _find(f"{run}-e")])
    print("corrected, same number:", _send(valid, f"{run}-f"))

    print("\n# Reported as paid twice")
    invoice = _find(f"{run}-a")[0]
    paid = {"invoice_id": invoice["id"], "status_code": "fr:212"}
    print("first:", _post("/invoice_events", json=paid))
    print("events:", _codes(api.get_invoice(invoice["id"])))
    print("again:", _post("/invoice_events", json=paid))

    time.sleep(10)
    print("\n# In the end")
    for suffix in "abcdef":
        for sent_invoice in _find(f"{run}-{suffix}"):
            number = api.invoice_number(sent_invoice)
            print(suffix, sent_invoice["id"], number, _codes(sent_invoice))
    print("original as sent:", api.get_original_invoice(invoice["id"]) == first)


if __name__ == "__main__":
    main()
