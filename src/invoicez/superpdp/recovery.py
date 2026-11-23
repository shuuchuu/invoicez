"""Find out what became of a request SuperPDP did not answer."""

import time
from collections.abc import Callable

from invoicez.exceptions import InvoicezError

# How long to look: it can take tens of seconds for the outcome to show.
RECOVERY_WAIT_SECONDS = 60.0
RECOVERY_POLL_SECONDS = 5.0


def look_for[T](find: Callable[[], T | None]) -> T | None:
    """Look for something until it is found, or for too long.

    Args:
        find: Returns what is looked for, or None. Its InvoicezErrors are ignored:
            whatever broke the request may take time to recover.

    Returns:
        What was found, or None.
    """
    deadline = time.monotonic() + RECOVERY_WAIT_SECONDS
    while True:
        try:
            found = find()
        except InvoicezError:
            found = None
        if found is not None or time.monotonic() >= deadline:
            return found
        time.sleep(RECOVERY_POLL_SECONDS)
