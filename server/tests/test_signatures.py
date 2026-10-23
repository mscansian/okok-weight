import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from scale_server import signatures
from scale_server.config import Settings
from tests.conftest import GATEWAY_ADDRESS, GATEWAY_KEY, Delivery, PingBuilder, WeighInBuilder


def test_given_a_request_when_signed_then_the_digest_is_the_agreed_one() -> None:
    """The gateway computes this with mbedTLS and the server with hashlib, and
    nothing lands unless the two arrive at the same string — so the string is
    worth pinning here, where a change to either side shows up as a failing test
    rather than as a board that has quietly stopped delivering.

    The digest is HMAC-SHA-256, under GATEWAY_KEY, of these exact bytes:

        POST\n/api/v1/devices/a0:b7:65:12:34:56/ping\n{"ping":true}
    """
    signed = signatures.sign(GATEWAY_KEY, "POST", b"/api/v1/devices/a0:b7:65:12:34:56/ping", b'{"ping":true}')

    assert signed == "sha256=6912208f879a4a332522377178e73dcfa57b9aed635ae4cb0c275f3da6c9e4cb"


def test_given_a_signed_weigh_in_when_posted_then_it_is_stored(post: Delivery, weigh_in: WeighInBuilder) -> None:
    assert post(weigh_in(72.35)).status_code == 201


def test_given_an_unsigned_weigh_in_when_posted_then_it_is_refused(
    client: TestClient, weigh_in: WeighInBuilder
) -> None:
    response = client.post("/api/v1/readings", json=weigh_in(72.35))

    assert response.status_code == 401
    assert client.get("/api/v1/readings").json()["total"] == 0


def test_given_a_weigh_in_signed_with_another_key_when_posted_then_it_is_refused(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    assert post(weigh_in(72.35), key="a" * 64).status_code == 401


def test_given_a_body_altered_after_signing_when_posted_then_it_is_refused(
    client: TestClient, weigh_in: WeighInBuilder
) -> None:
    """Someone between the gateway and the server can see a weigh-in go past and
    change what it says. The signature is over the bytes, so the change is what
    breaks it."""
    signed = json.dumps(weigh_in(72.35)).encode()
    tampered = signed.replace(b"72.35", b"62.35")

    response = client.post(
        "/api/v1/readings",
        content=tampered,
        headers={
            "Content-Type": "application/json",
            signatures.SIGNATURE_HEADER: signatures.sign(GATEWAY_KEY, "POST", b"/api/v1/readings", signed),
        },
    )

    assert response.status_code == 401


def test_given_a_malformed_signature_when_posted_then_it_is_refused(
    client: TestClient, weigh_in: WeighInBuilder
) -> None:
    """A header that is not a hex digest at all has to be turned away like any
    other wrong one, rather than failing somewhere inside the comparison."""
    for offered in ("", "sha256=", "nonsense", "sha256=zz", "sha256=" + "f" * 63):
        response = client.post(
            "/api/v1/readings",
            content=json.dumps(weigh_in(72.35)).encode(),
            headers={"Content-Type": "application/json", signatures.SIGNATURE_HEADER: offered},
        )

        assert response.status_code == 401, offered


def test_given_a_signature_that_is_not_text_when_verified_then_it_is_refused(client: TestClient) -> None:
    """Header bytes reach the application decoded as latin-1, so anything above
    0x7F arrives as a character that is not a hex digit and cannot be one. It has
    to come back as a refusal like any other wrong signature, rather than as an
    error raised inside the comparison. No HTTP client will send such a header,
    so the check is made against the scope it would arrive in."""

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/readings",
            "headers": [(b"x-signature", b"sha256=caf\xe9")],
            "client": ("192.0.2.10", 5000),
            "app": client.app,
        },
        receive,
    )

    with pytest.raises(HTTPException) as refusal:
        asyncio.run(signatures.verify_signature(request))

    assert refusal.value.status_code == 401


def test_given_a_ping_signed_for_one_gateway_when_replayed_at_another_then_it_is_refused(
    client: TestClient, device_ping: PingBuilder
) -> None:
    """A ping names the board it speaks for in its path. Were the signature over
    the body alone, a capture of one gateway's ping would keep any address the
    replayer chose looking alive."""
    body = device_ping()
    content = json.dumps(body).encode()
    signed_for = f"/api/v1/devices/{GATEWAY_ADDRESS}/ping"

    response = client.post(
        "/api/v1/devices/a0:b7:65:99:99:99/ping",
        content=content,
        headers={
            "Content-Type": "application/json",
            signatures.SIGNATURE_HEADER: signatures.sign(GATEWAY_KEY, "POST", signed_for.encode(), content),
        },
    )

    assert response.status_code == 401
    assert client.get("/api/v1/devices").json()["total"] == 0


def test_given_a_key_too_short_to_be_a_secret_when_the_server_starts_then_it_refuses() -> None:
    with pytest.raises(ValidationError):
        Settings(db_path="weights.db", gateway_key="hunter2")  # pyright: ignore[reportArgumentType]
