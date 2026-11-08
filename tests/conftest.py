import os
from pathlib import Path
from typing import Any

from pytest import Config, fixture

# What git exports to a hook to say which repository it runs on. In a linked
# worktree GIT_DIR is absolute, so the git repositories the tests create in
# temporary directories would act on invoicez's own instead (a `git init` there
# once set its core.bare).
_REPOSITORY_VARIABLES = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_WORK_TREE",
)


def pytest_configure(config: Config) -> None:
    # Before any test or fixture runs git, the pre-commit hook's included.
    for variable in _REPOSITORY_VARIABLES:
        os.environ.pop(variable, None)


@fixture(autouse=True)
def config_dirs(tmp_path_factory: Any, monkeypatch: Any) -> Path:
    """Keep every test away from the user's config directories.

    Loading the calendar moves invoicez's old Google OAuth files to schedz's
    directory, and logging in reads and writes there: on a real machine, that
    is the user's own credentials.

    Returns:
        The directory standing for the user config root.
    """
    root = tmp_path_factory.mktemp("config")

    def fake_user_config_path(app_name: str) -> Path:
        return root / app_name

    from invoicez.gcal import loading

    monkeypatch.setattr(loading, "user_config_path", fake_user_config_path)
    try:
        from schedz import google
    except ImportError:
        return root
    monkeypatch.setattr(google, "user_config_path", fake_user_config_path)
    return root
