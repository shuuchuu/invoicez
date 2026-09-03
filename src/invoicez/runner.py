from pathlib import Path

from facturx import generate_from_binary
from yaml import safe_load

from invoicez.building.pdf.builder import build_pdf
from invoicez.building.pdf.environment import create_environment, resolve_template
from invoicez.building.xml.builder import build_xml
from invoicez.cancellation import resolve_cancellation
from invoicez.exceptions import InvoicezError
from invoicez.variables import Variables


def run(
    path: Path,
    template: str,
    variables_path: Path,
    jinja2_dir: Path,
    git_dir: Path,
    assets_dir: Path,
    build_dir: Path,
    pdf_dir: Path,
) -> None:
    # Load global and invoice/quote data and merge both
    if not path.is_file():
        msg = f"Could not find the file to compile {path}."
        raise InvoicezError(msg)
    global_data = safe_load(variables_path.read_text(encoding="utf8"))
    document_data = safe_load(path.read_text(encoding="utf8"))
    document_data = resolve_cancellation(document_data, git_dir)
    variables = Variables.from_mappings(global_data, document_data)

    name = path.with_suffix("").name

    # Build PDF
    environment = create_environment(jinja2_dir)
    pdf_bytes = build_pdf(
        name,
        template,
        resolve_template(template, jinja2_dir),
        variables,
        environment,
        build_dir,
        assets_dir,
    )

    # Build XML
    xml_bytes = build_xml(name, variables, build_dir)

    # Build PDF/A-3 (PDF with embedded XML)
    pdf_path = pdf_dir / f"{name}.pdf"
    pdf_path.parent.mkdir(exist_ok=True, parents=True)
    pdf_path.write_bytes(generate_from_binary(pdf_bytes, xml_bytes))
