from pathlib import Path

from fastapi.testclient import TestClient

from scale_server.config import Settings
from scale_server.main import create_app
from tests.conftest import GATEWAY_ADDRESS, GATEWAY_KEY, Delivery, PingBuilder, WeighInBuilder, deliver


def test_given_no_request_id_when_a_request_is_made_then_one_is_returned(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert len(response.headers["X-Request-ID"]) == 36


def test_given_a_request_id_when_a_request_is_made_then_it_is_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "gateway-42"})

    assert response.headers["X-Request-ID"] == "gateway-42"


def test_given_a_malformed_request_id_when_a_request_is_made_then_it_is_replaced(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": 'id" injected="yes'})

    assert response.headers["X-Request-ID"] != 'id" injected="yes'


def test_given_an_oversized_body_when_posted_then_it_is_refused(post: Delivery, weigh_in: WeighInBuilder) -> None:
    body = weigh_in(72.0) | {"address": "08:B8:D0:12:34:56" + "0" * 5000}

    assert post(body).status_code == 413


def test_given_the_rate_limit_is_reached_when_posting_again_then_it_is_refused(
    device_ping: PingBuilder, tmp_path: Path
) -> None:
    settings = Settings(db_path=tmp_path / "weights.db", gateway_key=GATEWAY_KEY, rate_limit_per_minute=2)
    path = f"/api/v1/devices/{GATEWAY_ADDRESS}/ping"

    with TestClient(create_app(settings)) as limited:
        assert deliver(limited, path, device_ping()).status_code == 200
        assert deliver(limited, path, device_ping()).status_code == 200

        refused = deliver(limited, path, device_ping())

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"


def test_given_no_address_header_when_a_gateway_pings_then_the_peer_is_recorded(
    ping: Delivery, client: TestClient, device_ping: PingBuilder
) -> None:
    ping(device_ping())

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["client_ip"] == "testclient"


def test_given_a_proxy_names_the_caller_when_a_gateway_pings_then_it_is_recorded(
    ping: Delivery, client: TestClient, device_ping: PingBuilder
) -> None:
    ping(device_ping(), headers={"X-Real-IP": "203.0.113.7"})

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["client_ip"] == "203.0.113.7"


def test_given_a_cdn_in_front_of_the_proxy_when_a_gateway_pings_then_the_cdn_is_believed(
    ping: Delivery, client: TestClient, device_ping: PingBuilder
) -> None:
    """nginx sets `X-Real-IP` to the address it was called from, which behind a
    CDN is the CDN. Reading that first would name an edge as the caller."""
    ping(device_ping(), headers={"CF-Connecting-IP": "203.0.113.7", "X-Real-IP": "198.51.100.4"})

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["client_ip"] == "203.0.113.7"


def test_given_an_address_header_carrying_nonsense_when_a_gateway_pings_then_it_is_passed_over(
    ping: Delivery, client: TestClient, device_ping: PingBuilder
) -> None:
    """A proxy that sets these sets an address. Admitting anything else would
    give whoever sent it an allowance keyed to a string of their choosing."""
    ping(device_ping(), headers={"CF-Connecting-IP": "not-an-address", "X-Real-IP": "203.0.113.7"})

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["client_ip"] == "203.0.113.7"


def test_given_a_forwarded_for_chain_when_a_gateway_pings_then_its_head_is_recorded(
    ping: Delivery, client: TestClient, device_ping: PingBuilder
) -> None:
    """A proxy appends itself as it passes a request on, so the head of the list
    is the hop furthest from here."""
    ping(device_ping(), headers={"X-Forwarded-For": "203.0.113.7, 198.51.100.4"})

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["client_ip"] == "203.0.113.7"


def test_given_two_callers_behind_one_proxy_when_the_limit_is_reached_then_it_is_theirs_alone(
    device_ping: PingBuilder, tmp_path: Path
) -> None:
    """The point of reading the header: behind a proxy every request has the
    same peer, so without it one caller\'s traffic spends the gateway\'s."""
    settings = Settings(db_path=tmp_path / "weights.db", gateway_key=GATEWAY_KEY, rate_limit_per_minute=1)
    path = f"/api/v1/devices/{GATEWAY_ADDRESS}/ping"

    with TestClient(create_app(settings)) as proxied:
        spender = {"X-Real-IP": "203.0.113.7"}
        assert deliver(proxied, path, device_ping(), headers=spender).status_code == 200
        assert deliver(proxied, path, device_ping(), headers=spender).status_code == 429

        assert deliver(proxied, path, device_ping(), headers={"X-Real-IP": "198.51.100.4"}).status_code == 200
