import json
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from scale_server import signatures
from scale_server.config import Settings
from scale_server.main import create_app

WeighInBuilder = Callable[[float], dict[str, object]]
PingBuilder = Callable[..., dict[str, object]]
Delivery = Callable[..., httpx2.Response]
SignedPost = Callable[..., httpx2.Response]

GATEWAY_ADDRESS = "a0:b7:65:12:34:56"

# Long enough to satisfy the floor the server holds a real key to.
GATEWAY_KEY = "0f6b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "weights.db", gateway_key=GATEWAY_KEY, rate_limit_per_minute=1000, max_page_size=100
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def deliver(
    client: TestClient,
    path: str,
    body: dict[str, object],
    key: str = GATEWAY_KEY,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    """Posts the way the gateway does: signing the exact request it sends, which
    is what makes the signature something both sides can arrive at."""
    content = json.dumps(body).encode()
    signature = signatures.sign(key, "POST", path.encode(), content)
    return client.post(
        path,
        content=content,
        headers={"Content-Type": "application/json", signatures.SIGNATURE_HEADER: signature} | (headers or {}),
    )


@pytest.fixture
def signed_post(client: TestClient) -> SignedPost:
    def send(path: str, body: dict[str, object], key: str = GATEWAY_KEY) -> httpx2.Response:
        return deliver(client, path, body, key)

    return send


@pytest.fixture
def post(client: TestClient) -> Delivery:
    def send(body: dict[str, object], key: str = GATEWAY_KEY) -> httpx2.Response:
        return deliver(client, "/api/v1/readings", body, key)

    return send


@pytest.fixture
def ping(client: TestClient) -> Delivery:
    """A ping from the one gateway most tests need, which is the common case:
    a board reporting for itself at its own address."""

    def send(body: dict[str, object], key: str = GATEWAY_KEY, headers: dict[str, str] | None = None) -> httpx2.Response:
        return deliver(client, f"/api/v1/devices/{GATEWAY_ADDRESS}/ping", body, key, headers)

    return send


@pytest.fixture
def device_ping() -> PingBuilder:
    """A body shaped like the one the gateway pings with."""

    def build(**overrides: object) -> dict[str, object]:
        return {
            "scale_addresses": ["08:B8:D0:12:34:56"],
            "ssid": "your-network",
            "wifi_rssi": -58,
            "queued": 0,
            "uptime_ms": 84_213_000,
            "interval_ms": 300_000,
            "firmware_version": "0.1.0",
            "reset_reason": "power-on",
        } | overrides

    return build


@pytest.fixture
def weigh_in() -> WeighInBuilder:
    """A body shaped like the one the gateway posts, with a fresh event id per
    call so tests read as separate trips to the scale unless they say otherwise."""

    def build(weight_kg: float) -> dict[str, object]:
        return {
            "event_id": str(uuid4()),
            "weight_kg": weight_kg,
            "field_b": 6000,
            "sequence": 39,
            "rssi": -70,
            "address": "08:B8:D0:12:34:56",
        }

    return build
