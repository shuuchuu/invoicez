from logging import INFO, basicConfig

from rich.traceback import install


def setup_logging() -> None:
    install(show_locals=False)
    basicConfig(
        level=INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
    )
