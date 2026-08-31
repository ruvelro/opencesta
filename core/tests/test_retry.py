"""A transient network blip must not cost a whole day's snapshot.

This is not hypothetical: on 2026-08-31 a single `httpx.ReadTimeout` partway
through the alc1 walk aborted the job, and the matrix's fail-fast then cancelled
the Dia snapshot, so nothing was published that day.
"""

import httpx
import pytest

from opencesta.retry import is_transient, with_retry


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    return httpx.HTTPStatusError(
        str(code), request=request, response=httpx.Response(code, request=request)
    )


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr("opencesta.retry.time.sleep", lambda _: None)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ReadTimeout("slow"), True),
        (httpx.ConnectError("reset"), True),
        (status_error(503), True),
        (status_error(429), True),  # rate limited: back off, don't give up
        (status_error(404), False),  # an answer, not a failure
        (status_error(403), False),
        (ValueError("bad payload"), False),
    ],
)
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


def test_succeeds_after_a_transient_failure():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("boom")
        return "ok"

    assert with_retry(flaky) == "ok"
    assert len(calls) == 3


def test_gives_up_after_the_last_attempt():
    calls = []

    def always_fails():
        calls.append(1)
        raise httpx.ReadTimeout("boom")

    with pytest.raises(httpx.ReadTimeout):
        with_retry(always_fails, attempts=3)
    assert len(calls) == 3


def test_a_404_is_raised_immediately_without_retrying():
    """Retrying a definitive answer just wastes the chain's capacity and ours."""
    calls = []

    def not_found():
        calls.append(1)
        raise status_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        with_retry(not_found)
    assert len(calls) == 1


def test_no_retry_on_success():
    calls = []
    assert with_retry(lambda: calls.append(1) or "v") == "v"
    assert len(calls) == 1
