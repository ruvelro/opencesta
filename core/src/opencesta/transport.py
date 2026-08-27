from __future__ import annotations

import gzip
import urllib.error
import urllib.request
import zlib

import httpx


def _to_response(status: int, headers, body: bytes) -> httpx.Response:
    """Decode the body and drop the encoding headers that no longer describe it.

    urllib hands back the compressed bytes; httpx.Response treats `content` as
    already-decoded, so leaving `content-encoding` in place would make it try to
    decode twice.
    """
    items = list(headers)
    encoding = next(
        (v.lower().strip() for k, v in items if k.lower() == "content-encoding"), None
    )
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body, -zlib.MAX_WBITS)
    kept = [
        (k, v)
        for k, v in items
        if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
    ]
    return httpx.Response(status, headers=kept, content=body)


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
                return _to_response(resp.status, resp.headers.items(), resp.read())
        except urllib.error.HTTPError as exc:
            return _to_response(exc.code, exc.headers.items(), exc.read())
        except urllib.error.URLError as exc:
            raise httpx.ConnectError(str(exc.reason), request=request) from exc
        except TimeoutError as exc:
            raise httpx.ReadTimeout(str(exc), request=request) from exc
