from typing import Annotated, Any

from cyclopts import Parameter
from rich.console import Console
from rich.text import Text

from invoicez.superpdp.config import log_path
from invoicez.superpdp.errors import REQUEST_ID_HEADER
from invoicez.superpdp.http import Exchange, read_log

# What creates something, shown without --all.
_CREATING = ("/invoices", "/invoice_events")


def _creates(exchange: Exchange) -> bool:
    path = str(exchange.request.get("path"))
    return exchange.request.get("method") == "POST" and path.endswith(_CREATING)


def _mentions(exchange: Exchange, search: str) -> bool:
    records = (exchange.request, exchange.answer or {})
    return any(
        search.casefold() in str(value).casefold()
        for record in records
        for name, value in record.items()
        if name not in {"key", "seconds"}
    )


def _fields(record: dict[str, Any], *excluded: str) -> str:
    return " ".join(
        f"{name}={value}" for name, value in record.items() if name not in excluded
    )


def _request_text(exchange: Exchange) -> str:
    sent = exchange.request
    text = f"{sent.get('at')} {sent.get('method')} {sent.get('path')}"
    return f"{text} {_fields(sent, 'key', 'at', 'method', 'path')}".rstrip()


def _answer_text(exchange: Exchange) -> str:
    answer = exchange.answer
    if answer is None:
        return "no answer logged (interrupted, or still running)"
    if "error" in answer:
        return f"no answer after {answer.get('seconds')}s, {answer['error']}"
    text = f"HTTP {answer.get('status')} after {answer.get('seconds')}s"
    if "request_id" in answer:
        text += f", {REQUEST_ID_HEADER}: {answer['request_id']}"
    if "id" in answer:
        text += f", id {answer['id']}"
    if "message" in answer:
        text += f", {answer['message']}"
    return text


def log(
    search: str | None = None,
    *,
    show_all: Annotated[bool, Parameter(name="--all", negative="")] = False,
    limit: int = 20,
) -> None:
    """Show the requests made to SuperPDP, to tell it which ones went wrong.

    By default, only those creating something (invoices, payments) and those that
    failed are shown. Times are in UTC.

    Args:
        search: Show every request mentioning this, such as the number of an invoice,
            its SuperPDP id, an X-Request-ID, or a date.
        show_all: Also show the requests that only read, log in, or validate.
        limit: How many requests to show, the latest ones.
    """
    console = Console()
    exchanges = [
        exchange
        for exchange in read_log()
        if (
            _mentions(exchange, search)
            if search is not None
            else show_all or exchange.failed or _creates(exchange)
        )
    ]
    shown = exchanges[-limit:] if limit > 0 else []
    for exchange in shown:
        style = "red" if exchange.failed else "green"
        console.print(Text(_request_text(exchange)), soft_wrap=True)
        console.print(Text(f"  {_answer_text(exchange)}", style), soft_wrap=True)
    summary = f"{len(shown)} of {len(exchanges)} requests, from {log_path()}"
    console.print(Text(summary, "dim"), soft_wrap=True)
