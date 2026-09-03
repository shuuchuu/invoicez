from pathlib import Path

from facturx import generate_xml

from invoicez.building.xml.variables_to_bt_converter import convert
from invoicez.variables import Variables


def build_xml(name: str, variables: Variables, build_dir: Path) -> bytes:
    xml_bytes = generate_xml(convert(variables), level="en16931")
    build_dir.mkdir(exist_ok=True, parents=True)
    (build_dir / name).with_suffix(".xml").write_bytes(xml_bytes)
    return xml_bytes
