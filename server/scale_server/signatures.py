"""Proving a request came from something holding the gateway's key.

The gateway signs the request it is about to send and the server signs the one
it received, both with a key the two share. They agree only if the request
arrived as it left and the sender held the key. The key never travels, so
listening to the network yields nothing that can be turned into a delivery of
someone else's making.

The digest covers the method and the request target as well as the body, because
a route's path carries meaning of its own: a ping names the gateway it speaks
for there, and a signature over the body alone would let a captured ping be
replayed at another gateway's path and answer for a board that is not there.

A capture replayed at the path it was signed for still stands, and nothing here
stops that. For a weigh-in it costs nothing, since the repeat carries the
`event_id` of the reading it already made. For a ping it does cost something: a
replay keeps a gateway looking alive after it has stopped. The usual defence — a
signed timestamp held to a window — is the one this gateway cannot mount,
because it has no clock to read.
"""

import hashlib
import hmac
import logging

from fastapi import HTTPException, Request

from scale_server.middleware import client_ip

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Signature"

# The digest names itself in what it produces, so a second algorithm could be
# accepted alongside this one rather than having to replace it on the gateway and
# the server in the same moment.
_SHA256_PREFIX = "sha256="


def sign(key: str, method: str, target: bytes, body: bytes) -> str:
    """What the signature header carries for this request.

    The three parts are joined by a byte none of them can contain, so no request
    can be dressed up as another by moving the boundary between them.
    """
    signed = method.encode("ascii") + b"\n" + target + b"\n" + body
    return _SHA256_PREFIX + hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()


def request_target(request: Request) -> bytes:
    """The path and query as they arrived, which is what the gateway signed.

    Taken raw rather than off the parsed url: the gateway signs the bytes it puts
    on the wire, and a path this side has already decoded is no longer those.
    """
    path = request.scope.get("raw_path") or request.url.path.encode()
    query = request.scope.get("query_string", b"")
    return path + b"?" + query if query else path


async def verify_signature(request: Request) -> None:
    """Turns away a request whose signature is missing, malformed or wrong."""
    expected = sign(
        request.app.state.settings.gateway_key, request.method, request_target(request), await request.body()
    )
    offered = request.headers.get(SIGNATURE_HEADER, "")

    if not hmac.compare_digest(offered.encode("utf-8"), expected.encode("ascii")):
        # The gateway is headless and a rejected delivery is retried for a day,
        # so this line is the only place a wrong key on either side shows up.
        log.warning("signature rejected", extra={"client_ip": client_ip(request), "signed": bool(offered)})
        raise HTTPException(status_code=401, detail="bad or missing signature")
