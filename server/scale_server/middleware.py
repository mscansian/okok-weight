"""Cross-cutting concerns every request passes through, outermost first."""

import ipaddress
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from scale_server import logs

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# A caller-supplied id lands in every log line for the request, so it is held to
# a shape that cannot forge structure once it gets there.
_SUPPLIED_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

CallNext = Callable[[Request], Awaitable[Response]]


class RequestContext(BaseHTTPMiddleware):
    """Gives the request an id, then sums it up in a single log line."""

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        current = supplied if _SUPPLIED_REQUEST_ID.match(supplied) else str(uuid.uuid4())
        request.state.request_id = current
        token = logs.request_id.set(current)
        started = time.perf_counter()

        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("request failed", extra=self._context(request, started))
                raise

            response.headers[REQUEST_ID_HEADER] = current
            log.info("request completed", extra=self._context(request, started) | {"status": response.status_code})
            return response
        finally:
            logs.request_id.reset(token)

    @staticmethod
    def _context(request: Request, started: float) -> dict[str, object]:
        return {
            "method": request.method,
            "path": request.url.path,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "client_ip": client_ip(request),
        }


class BodySizeLimit(BaseHTTPMiddleware):
    """Turns away oversized or unmeasured bodies before anything reads them."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        if request.method not in _BODY_METHODS:
            return await call_next(request)

        declared = request.headers.get("Content-Length")
        if declared is None or not declared.isdigit():
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)
        if int(declared) > self.max_bytes:
            return JSONResponse({"detail": "request body too large"}, status_code=413)

        return await call_next(request)


class RateLimit(BaseHTTPMiddleware):
    """A per-client ceiling on requests per minute.

    The counters live in this process, which is the whole deployment: one
    container serving one household's gateway. Anything wider would need them in
    a store every process can see.
    """

    _TRACKED_CLIENTS = 1024

    def __init__(self, app: ASGIApp, per_minute: int) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self._windows: OrderedDict[str, tuple[int, int]] = OrderedDict()

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        client = client_ip(request)
        window = int(time.monotonic() // 60)

        opened, used = self._windows.get(client, (window, 0))
        if opened != window:
            opened, used = window, 0

        if used >= self.per_minute:
            log.warning("rate limit exceeded", extra={"client_ip": client, "limit_per_minute": self.per_minute})
            return JSONResponse({"detail": "too many requests"}, status_code=429, headers={"Retry-After": "60"})

        self._windows[client] = (opened, used + 1)
        self._windows.move_to_end(client)
        while len(self._windows) > self._TRACKED_CLIENTS:
            self._windows.popitem(last=False)

        return await call_next(request)


# Where the caller's address is looked for, outermost proxy first, before
# falling back to the address the connection came from.
#
# The first entry of each is taken, which is the convention `X-Forwarded-For`
# sets: a proxy appends itself as it passes a request on, so the head of the
# list is the hop furthest from this server. Single-valued headers have no list
# and are read whole.
#
# The order matters. nginx sets `X-Real-IP` to the address it was called from,
# which behind Cloudflare is Cloudflare's, so reading it before the CDN's own
# header would name an edge as the visitor.
#
# The head of a chain is whatever the caller put there, so any of these can be
# made up. That is accepted rather than overlooked: what the address feeds is a
# rate limit and a log line, and nothing is admitted on the strength of it — a
# weigh-in and a ping get in by their signature, from whatever address.
ADDRESS_HEADERS = ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For")


def client_ip(request: Request) -> str:
    """Who the rate limit holds and the logs name.

    Behind a reverse proxy every request arrives from the proxy, so the peer
    address names it rather than the caller and one allowance is shared out among
    everyone reaching the server through it — which is one busy caller away from
    spending the gateway's.
    """
    for header in ADDRESS_HEADERS:
        supplied = request.headers.get(header, "").split(",")[0].strip()
        if not supplied:
            continue
        try:
            return str(ipaddress.ip_address(supplied))
        except ValueError:
            # A proxy that sets these sets an address, so anything else is a
            # header nobody meant to send and the next one is worth more.
            log.warning("address header is not an address", extra={"header": header})

    return request.client.host if request.client else "unknown"
