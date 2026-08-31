"""Shared retry policy for the chain adapters.

A full snapshot is thousands of sequential requests over several minutes, so a
single dropped connection anywhere in the walk used to abort the whole run and
cost that day's data. Transient failures — timeouts, connection resets, 5xx —
are worth retrying; a 404 is an answer, not a failure, and must surface at once.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

TRANSIENT = (httpx.TimeoutException, httpx.TransportError)


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, TRANSIENT)


def with_retry(call: Callable[[], T], attempts: int = 3, base_delay: float = 2.0) -> T:
    """Run `call`, retrying transient failures with exponential backoff.

    Non-transient errors (4xx other than 429, parse errors) are re-raised
    immediately: retrying them just wastes the chain's capacity and ours.
    """
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if not is_transient(exc) or attempt == attempts - 1:
                raise
            time.sleep(base_delay**attempt)
    raise AssertionError("unreachable")
