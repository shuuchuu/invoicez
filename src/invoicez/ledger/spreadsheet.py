"""Read the sheets of an OpenDocument spreadsheet (.ods), as cached by its editor.

Formulas are not evaluated: their cells hold the values last computed by the
spreadsheet editor, saved with the file.
"""

from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from invoicez.exceptions import InvoicezError

_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_CELLS = (f"{_TABLE}table-cell", f"{_TABLE}covered-table-cell")
_NUMBER_TYPES = frozenset(("float", "currency", "percentage"))

Row = dict[str, Any]


def _value(cell: ElementTree.Element) -> Any:
    kind = cell.get(f"{_OFFICE}value-type")
    if kind in _NUMBER_TYPES:
        return float(cell.get(f"{_OFFICE}value", "0"))
    if kind == "date":
        return date.fromisoformat(cell.get(f"{_OFFICE}date-value", "")[:10])
    if kind is None:
        return None
    # Paragraphs of the cell, one per line.
    return "\n".join("".join(p.itertext()) for p in cell if p.tag.endswith("}p"))


def _cells(row: ElementTree.Element) -> list[Any]:
    values: list[Any] = []
    for cell in row:
        if cell.tag not in _CELLS:
            continue
        value = _value(cell)
        repeat = int(cell.get(f"{_TABLE}number-columns-repeated", "1"))
        # Trailing empty cells are repeated up to the last column of the sheet.
        values.extend([value] * (repeat if value is not None else min(repeat, 1)))
    return values


def read_sheets(path: Path) -> dict[str, list[Row]]:
    """Read every sheet whose first row names its columns.

    Args:
        path: The .ods file.

    Returns:
        The rows holding at least one value, by sheet name, each mapping the
        names of the first row to the values below them.

    Raises:
        InvoicezError: If the file is missing or not an OpenDocument spreadsheet.
    """
    try:
        with ZipFile(path) as archive:
            content = archive.read("content.xml")
    except (OSError, BadZipFile, KeyError) as e:
        msg = f"{path}: not a readable OpenDocument spreadsheet ({e})"
        raise InvoicezError(msg) from e
    sheets: dict[str, list[Row]] = {}
    for table in ElementTree.fromstring(content).iter(f"{_TABLE}table"):
        header: list[Any] | None = None
        rows: list[Row] = []
        for row in table.iter(f"{_TABLE}table-row"):
            values = _cells(row)
            if header is None:
                header = values
                continue
            if any(value is not None for value in values):
                repeat = int(row.get(f"{_TABLE}number-rows-repeated", "1"))
                named = {
                    str(name): value
                    for name, value in zip(header, values, strict=False)
                    if name is not None
                }
                rows.extend(dict(named) for _ in range(repeat))
        sheets[table.get(f"{_TABLE}name", "")] = rows
    return sheets
