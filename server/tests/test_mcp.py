import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx2
from fastapi.testclient import TestClient

from scale_server import database, readings
from scale_server.config import Settings
from tests.conftest import GATEWAY_ADDRESS, MCP_TOKEN, Delivery, PingBuilder

SCALE = "08:b8:d0:12:34:56"
OTHER_SCALE = "08:b8:d0:65:43:21"

DAY_MS = 24 * 60 * 60 * 1000


def rpc(client: TestClient, method: str, params: dict[str, Any], token: str = MCP_TOKEN) -> httpx2.Response:
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
    )


def call(client: TestClient, tool: str, **arguments: object) -> dict[str, Any]:
    response = rpc(client, "tools/call", {"name": tool, "arguments": arguments})
    assert response.status_code == 200
    return response.json()["result"]


def result_of(client: TestClient, tool: str, **arguments: object) -> Any:
    result = call(client, tool, **arguments)
    assert result["isError"] is False, result["content"]
    return result["structuredContent"]


def weigh(settings: Settings, weight_kg: float, days_ago: float, scale: str = SCALE) -> None:
    """A weigh-in taken some time ago, the way a gateway holding it would report it."""
    connection = database.connect(settings.db_path)
    try:
        readings.record(connection, uuid4(), scale, weight_kg, age_ms=int(days_ago * DAY_MS))
    finally:
        connection.close()


def days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def test_given_no_token_when_the_mcp_endpoint_is_called_then_it_is_rejected(client: TestClient) -> None:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_given_a_wrong_token_when_the_mcp_endpoint_is_called_then_it_is_rejected(client: TestClient) -> None:
    assert rpc(client, "tools/list", {}, token="not-the-token").status_code == 401


def test_given_the_token_when_tools_are_listed_then_every_tool_is_read_only(client: TestClient) -> None:
    tools = rpc(client, "tools/list", {}).json()["result"]["tools"]

    assert {tool["name"] for tool in tools} == {"get_latest_weigh_in", "list_weigh_ins", "list_devices", "get_device"}
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)


def test_given_weigh_ins_when_the_latest_is_asked_for_then_only_the_newest_comes_back(
    client: TestClient, settings: Settings
) -> None:
    weigh(settings, 80.0, days_ago=2)
    weigh(settings, 79.5, days_ago=1)

    latest = result_of(client, "get_latest_weigh_in")["result"]

    assert latest["weight_kg"] == 79.5
    assert set(latest) == {"measured_at", "weight_kg"}
    assert datetime.fromisoformat(latest["measured_at"]).tzinfo is not None


def test_given_no_weigh_ins_when_the_latest_is_asked_for_then_it_is_null(client: TestClient) -> None:
    assert result_of(client, "get_latest_weigh_in")["result"] is None


def test_given_two_scales_when_the_latest_of_one_is_asked_for_then_the_other_is_ignored(
    client: TestClient, settings: Settings
) -> None:
    weigh(settings, 80.0, days_ago=2, scale=SCALE)
    weigh(settings, 60.0, days_ago=1, scale=OTHER_SCALE)

    latest = result_of(client, "get_latest_weigh_in", scale_address=SCALE.upper())["result"]

    assert latest["weight_kg"] == 80.0


def test_given_weigh_ins_around_a_range_when_it_is_listed_then_only_those_inside_come_back_newest_first(
    client: TestClient, settings: Settings
) -> None:
    weigh(settings, 82.0, days_ago=6)
    weigh(settings, 81.0, days_ago=4)
    weigh(settings, 80.0, days_ago=3)
    weigh(settings, 79.0, days_ago=1)

    history = result_of(client, "list_weigh_ins", start=days_ago(5), end=days_ago(2))

    assert [row["weight_kg"] for row in history["weigh_ins"]] == [80.0, 81.0]
    assert history["total"] == 2
    assert history["truncated"] is False


def test_given_more_weigh_ins_than_the_limit_when_listed_then_the_newest_come_back_and_the_summary_covers_all(
    client: TestClient, settings: Settings
) -> None:
    weigh(settings, 82.0, days_ago=4)
    weigh(settings, 81.0, days_ago=3)
    weigh(settings, 80.0, days_ago=2)
    weigh(settings, 78.0, days_ago=1)

    history = result_of(client, "list_weigh_ins", start=days_ago(5), end=days_ago(0), limit=2)

    assert [row["weight_kg"] for row in history["weigh_ins"]] == [78.0, 80.0]
    assert history["total"] == 4
    assert history["truncated"] is True
    summary = history["summary"]
    assert summary["count"] == 4
    assert summary["lowest_kg"] == 78.0
    assert summary["highest_kg"] == 82.0
    assert summary["average_kg"] == 80.25
    assert summary["earliest"]["weight_kg"] == 82.0
    assert summary["latest"]["weight_kg"] == 78.0


def test_given_an_empty_range_when_listed_then_there_is_no_summary(client: TestClient) -> None:
    history = result_of(client, "list_weigh_ins", start=days_ago(5), end=days_ago(0))

    assert history == {"weigh_ins": [], "total": 0, "truncated": False}


def test_given_a_start_after_the_end_when_listed_then_the_tool_says_so(client: TestClient) -> None:
    result = call(client, "list_weigh_ins", start=days_ago(1), end=days_ago(2))

    assert result["isError"] is True
    assert "start is after end" in result["content"][0]["text"]


def test_given_a_datetime_without_an_offset_when_listed_then_it_is_rejected(client: TestClient) -> None:
    result = call(client, "list_weigh_ins", start="2026-10-01T00:00:00", end="2026-10-02T00:00:00")

    assert result["isError"] is True


def test_given_the_largest_page_when_listed_then_it_stays_within_the_size_budget(
    client: TestClient, settings: Settings
) -> None:
    for index in range(250):
        weigh(settings, 70 + index / 100, days_ago=index / 50)

    response = rpc(
        client,
        "tools/call",
        {"name": "list_weigh_ins", "arguments": {"start": days_ago(10), "end": days_ago(0), "limit": 200}},
    )

    assert len(response.json()["result"]["structuredContent"]["weigh_ins"]) == 200
    # About four characters to a token, and the result is sent twice: as text and
    # as structured content. 12k tokens keeps it well under what clients cut.
    assert len(response.text) < 48_000


def test_given_a_gateway_when_devices_are_listed_then_it_is_there_with_its_last_ping(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping())

    devices = result_of(client, "list_devices")["devices"]

    assert [device["address"] for device in devices] == [GATEWAY_ADDRESS]
    assert devices[0]["online"] is True
    assert "last_seen_at" in devices[0]
    # A nameless board has no name field, rather than a null one.
    assert "name" not in devices[0]


def test_given_a_gateway_when_its_detail_is_asked_for_then_everything_it_reported_comes_back_without_nulls(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping(free_heap=None))

    device = result_of(client, "get_device", address=GATEWAY_ADDRESS.upper())

    assert device["address"] == GATEWAY_ADDRESS
    assert device["firmware_version"] == "0.1.0"
    assert "free_heap" not in device
    assert None not in device.values()


def test_given_an_unknown_gateway_when_its_detail_is_asked_for_then_the_tool_says_so(client: TestClient) -> None:
    result = call(client, "get_device", address="aa:bb:cc:dd:ee:ff")

    assert result["isError"] is True
    assert "No gateway with that address" in result["content"][0]["text"]


def test_given_a_tool_result_when_read_as_text_then_it_matches_the_structured_content(
    client: TestClient, settings: Settings
) -> None:
    """Clients that ignore structured content read the text, so it carries the same."""
    weigh(settings, 79.5, days_ago=1)

    result = call(client, "list_weigh_ins", start=days_ago(2), end=days_ago(0))

    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
