"""The MCP endpoint an agent reads the weigh-in history through, at /mcp.

Stateless: every request stands alone, so a restart does not break a
conversation an agent has open.
"""

import hmac
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from scale_server import mcp_tools
from scale_server.config import Settings

log = logging.getLogger(__name__)

MCP_PATH = "/mcp"


def build(settings: Settings) -> tuple[MCPServer, Route]:
    """The server, whose session manager the host app's lifespan has to run, and
    the route that serves it behind the token."""
    server = MCPServer("okok-weight")
    mcp_tools.register(server, settings)

    transport = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        # The token is what admits a request here. A page that rebinds DNS to
        # reach this server cannot read the token, so the Host check adds
        # nothing and would reject every public hostname.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    # A route passes the path through unchanged, so the transport's own route at
    # the same path is the one that answers.
    return server, Route(MCP_PATH, endpoint=BearerToken(transport, settings.mcp_token.get_secret_value()))


class BearerToken:
    """Admits a request only when it carries `Authorization: Bearer <token>`."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        offered = dict(scope["headers"]).get(b"authorization", b"")
        if not hmac.compare_digest(offered, self.expected):
            log.warning("mcp token rejected", extra={"token_sent": bool(offered)})
            response = JSONResponse(
                {"detail": "bad or missing token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
