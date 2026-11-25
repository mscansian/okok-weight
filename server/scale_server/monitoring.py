"""Error monitoring: errors, logs and traces go to Sentry."""

import logging
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.mcp import MCPIntegration

from scale_server.config import Settings

log = logging.getLogger(__name__)

# The container's health check asks for this route every 30 seconds. A trace of
# it says nothing a failing health check would not.
HEALTH_CHECK_PATH = "/api/v1/health"


def start(settings: Settings) -> None:
    """Runs before the app is built, because the MCP integration hooks into the
    MCP server as it is created."""
    if settings.sentry_dsn is None:
        log.info("error monitoring off")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sampler=sample_trace,
        integrations=[
            # Every log line from INFO up goes to Sentry as a log, and every one
            # from ERROR up also opens an issue.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            MCPIntegration(),
        ],
        data_collection={
            # Bodies carry weigh-ins, and MCP results carry the whole history.
            "http_bodies": [],
            "mcp": {"inputs": False, "outputs": False},
            # The SDK filters Authorization by itself. The gateway's signature
            # is not a secret, but a captured one replays.
            "http_headers": {"request": {"mode": "denylist", "terms": ["signature"]}},
        },
    )
    log.info("error monitoring on")


def sample_trace(context: dict[str, Any]) -> float:
    """Every request is traced except the health check."""
    request = context.get("asgi_scope") or {}
    return 0.0 if request.get("path") == HEALTH_CHECK_PATH else 1.0
