"""The transport must pass headers through untouched.

This is a policy guarantee, not a style preference: silently adding a
browser-shaped `accept-encoding` would turn OpenCesta into something that
evades bot detection. See DATA_POLICY.md.
"""

import io
import urllib.error
from typing import ClassVar

import httpx
import pytest

from opencesta.transport import UrllibTransport


class FakeResponse(io.BytesIO):
    status = 200
    headers: ClassVar[dict] = {"content-type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.headers)
        seen["method"] = req.get_method()
        seen["url"] = req.full_url
        return FakeResponse(b"<html>ok</html>")

    monkeypatch.setattr("opencesta.transport.urllib.request.urlopen", fake_urlopen)
    return seen


def test_headers_pass_through_untouched(captured):
    client = httpx.Client(transport=UrllibTransport(), headers={"User-Agent": "OpenCesta/test"})
    resp = client.get("https://example.test/x")

    assert resp.status_code == 200
    assert resp.text == "<html>ok</html>"
    # urllib title-cases header names; compare case-insensitively.
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert sent["user-agent"] == "OpenCesta/test"
    # Whatever httpx chose to send is what went out — nothing invented here.
    assert sent.get("accept-encoding") == client.headers.get("accept-encoding")


def test_http_error_becomes_response_not_exception(monkeypatch):
    def raise_403(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"no"))

    monkeypatch.setattr("opencesta.transport.urllib.request.urlopen", raise_403)
    client = httpx.Client(transport=UrllibTransport())

    resp = client.get("https://example.test/x")
    assert resp.status_code == 403
    assert resp.text == "no"


def test_connection_error_maps_to_httpx(monkeypatch):
    def raise_url_error(req, timeout=None):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr("opencesta.transport.urllib.request.urlopen", raise_url_error)
    client = httpx.Client(transport=UrllibTransport())

    with pytest.raises(httpx.ConnectError):
        client.get("https://example.test/x")
