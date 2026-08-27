from __future__ import annotations

import urllib.error
import urllib.request

import httpx


class UrllibTransport(httpx.BaseTransport):
    """An httpx transport backed by the standard library's urllib.

    It forwards exactly the headers the caller set — it never adds, removes or
    rewrites any of them. That matters: Akamai (which fronts Dia) 403s on
    `accept-encoding: gzip, deflate` and allows the browser-shaped
    `gzip, deflate, br`, so a transport that quietly "fixed" headers would be
    evading bot detection. See DATA_POLICY.md.
    """

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        req = urllib.request.Request(
            str(request.url),
            data=request.content or None,
            headers={k.decode(): v.decode() for k, v in request.headers.raw},
            method=request.method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return httpx.Response(
                    resp.status, headers=list(resp.headers.items()), content=resp.read()
                )
        except urllib.error.HTTPError as exc:
            return httpx.Response(
                exc.code, headers=list(exc.headers.items()), content=exc.read()
            )
        except urllib.error.URLError as exc:
            raise httpx.ConnectError(str(exc.reason), request=request) from exc
        except TimeoutError as exc:
            raise httpx.ReadTimeout(str(exc), request=request) from exc
