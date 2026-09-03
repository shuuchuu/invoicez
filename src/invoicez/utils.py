import os
import sys
from pathlib import Path

from pydantic import BaseModel
from yaml import safe_load


def _unquote_file(url: str) -> str:
    from urllib.parse import unquote

    if url.startswith("file://"):
        url = unquote(url[7:])

    return url


def _open_url_windows(url: str, locate: bool) -> int:
    import subprocess

    if locate:
        url = _unquote_file(url)
        args = ["explorer", "/select,", url]
        try:
            return subprocess.call(args)
        except OSError:
            return 127
    else:
        try:
            os.startfile(url)  # ty: ignore[unresolved-attribute]
        except OSError:
            return 127
        return 0


def _open_url_darwin(url: str, *, wait: bool, locate: bool) -> int:
    import subprocess

    args = ["open"]
    if wait:
        args.append("-W")
    if locate:
        args.append("-R")
    args.append(_unquote_file(url))
    with Path("/dev/null").open("w") as null:
        return subprocess.Popen(args, stderr=null).wait()


def _open_url_cygwin(url: str, *, wait: bool, locate: bool) -> int:
    import subprocess

    if locate:
        url = _unquote_file(url)
        args = ["cygstart", str(Path(url).parent)]
    else:
        args = ["cygstart"]
        if wait:
            args.append("-w")
        args.append(url)
    try:
        return subprocess.call(args)
    except OSError:
        # Command not found
        return 127


def _open_url_rest(url: str, *, wait: bool, locate: bool) -> int:
    import subprocess

    try:
        if locate:
            url = str(Path(_unquote_file(url)).parent or ".")
        else:
            url = _unquote_file(url)
        c = subprocess.Popen(["xdg-open", url])
        if wait:
            return c.wait()
        return 0
    except OSError:
        if url.startswith(("http://", "https://")) and not locate and not wait:
            import webbrowser

            webbrowser.open(url)
            return 0
        return 1


def launch(url: str, *, wait: bool = False, locate: bool = False) -> int:
    """Launch the provided file.

    Adapted from the \
    [`click`](https://click.palletsprojects.com/en/stable/api/#click.launch) project. \
    This function launches the given URL (or filename) in the default viewer \
    application for this file type. If this is an executable, it might launch the \
    executable in a new session. The return value is the exit code of the launched \
    application. Usually, 0 indicates success.

    Args:
        url: URL or filename of the thing to launch.
        wait: Wait for the program to exit before returning. This only works if the \
            launched program blocks. In particular, xdg-open on Linux does not block.
        locate: If this is set to True then instead of launching the application \
            associated with the URL it will attempt to launch a file manager with the \
            file located. This might have weird effects if the URL does not point to \
            the filesystem.

    Returns:
        Return code of the executed program.
    """
    if sys.platform.startswith("win"):
        return _open_url_windows(url, locate)
    if sys.platform == "darwin":
        return _open_url_darwin(url, wait=wait, locate=locate)
    if sys.platform.startswith("cygwin"):
        return _open_url_cygwin(url, wait=wait, locate=locate)
    return _open_url_rest(url, wait=wait, locate=locate)


def load_model_from_yml_path[T: BaseModel](model_cls: type[T], path: Path) -> T:
    return model_cls.model_validate(safe_load(path.read_text(encoding="utf8")))
