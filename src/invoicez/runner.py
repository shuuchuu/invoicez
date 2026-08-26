from pathlib import Path

from invoicez.building import build, create_context, create_environment
from invoicez.config import get_config
from invoicez.paths import Paths
from invoicez.target import Target


def run(path: Path, template: str, paths: Paths) -> None:
    config = get_config(paths)
    target = Target(path, template, paths)
    context = create_context(target.data, config)
    environment = create_environment(paths.jinja2_dir)
    build(
        target.name,
        target.template_name,
        target.template_path,
        context,
        environment,
        paths.build_dir,
        paths.assets_dir,
        paths.pdf_dir,
    )
