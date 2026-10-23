from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from scale_server import database
from scale_server.config import Settings
from scale_server.database import TIMESTAMP_FORMAT
from tests.conftest import GATEWAY_ADDRESS, Delivery, PingBuilder, SignedPost


def last_seen(settings: Settings, address: str, at: datetime) -> None:
    """Moves a gateway's last ping back in time, which is the only way to reach
    the silence an offline device is recognised by without waiting it out."""
    with database.connect(settings.db_path) as connection:
        connection.execute(
            "UPDATE devices SET last_seen_at = ? WHERE address = ?", (at.strftime(TIMESTAMP_FORMAT), address)
        )


def recorded_at(settings: Settings, address: str, at: datetime) -> None:
    """Moves every ping a board has already sent back in time, which is the only
    way to reach an old history without waiting a week for one."""
    with database.connect(settings.db_path) as connection:
        connection.execute(
            "UPDATE device_pings SET recorded_at = ? WHERE address = ?", (at.strftime(TIMESTAMP_FORMAT), address)
        )


def test_given_a_gateway_that_has_never_pinged_when_it_pings_then_it_is_recorded(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    response = ping(device_ping())

    assert response.status_code == 200
    device = response.json()
    assert device["address"] == GATEWAY_ADDRESS
    assert device["online"] is True
    assert device["name"] is None
    assert device["queued"] == 0
    assert device["firmware_version"] == "0.1.0"
    assert device["first_seen_at"] == device["last_seen_at"]

    listed = client.get("/api/v1/devices").json()
    assert listed["total"] == 1
    assert listed["items"][0]["address"] == GATEWAY_ADDRESS


def test_given_a_gateway_that_has_pinged_before_when_it_pings_again_then_it_stays_one_device(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    first = ping(device_ping()).json()

    later = ping(device_ping(queued=3, wifi_rssi=-71)).json()

    assert later["queued"] == 3
    assert later["wifi_rssi"] == -71
    # The board arrived once and has been here since.
    assert later["first_seen_at"] == first["first_seen_at"]
    assert later["last_seen_at"] >= first["last_seen_at"]
    assert client.get("/api/v1/devices").json()["total"] == 1


def test_given_a_named_gateway_when_it_pings_again_then_the_name_survives(
    client: TestClient, settings: Settings, ping: Delivery, device_ping: PingBuilder
) -> None:
    """The name belongs to whoever put the board somewhere, so a ping — which
    knows nothing about that — must not write over it."""
    ping(device_ping())
    with database.connect(settings.db_path) as connection:
        connection.execute("UPDATE devices SET name = ? WHERE address = ?", ("bathroom", GATEWAY_ADDRESS))

    ping(device_ping(queued=1))

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["name"] == "bathroom"


def test_given_a_gateway_quiet_for_fewer_pings_than_it_is_allowed_when_asked_then_it_is_still_online(
    client: TestClient, settings: Settings, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping(interval_ms=300_000))

    last_seen(settings, GATEWAY_ADDRESS, datetime.now(UTC) - timedelta(minutes=20))

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["online"] is True


def test_given_a_gateway_quiet_for_longer_than_it_is_allowed_when_asked_then_it_is_offline(
    client: TestClient, settings: Settings, ping: Delivery, device_ping: PingBuilder
) -> None:
    """Five missed pings at the five minutes this board declares is half an hour
    of silence, and the settings say five is as many as it may miss."""
    ping(device_ping(interval_ms=300_000))

    last_seen(settings, GATEWAY_ADDRESS, datetime.now(UTC) - timedelta(minutes=30))

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["online"] is False


def test_given_a_gateway_that_pings_rarely_when_asked_then_its_own_cadence_is_what_judges_it(
    client: TestClient, settings: Settings, ping: Delivery, device_ping: PingBuilder
) -> None:
    """The threshold follows the interval the gateway declares. A board that
    speaks once an hour is not dead twenty minutes after it last did."""
    ping(device_ping(interval_ms=3_600_000))

    last_seen(settings, GATEWAY_ADDRESS, datetime.now(UTC) - timedelta(minutes=30))

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["online"] is True


def test_given_two_gateways_when_listed_then_the_one_heard_from_most_recently_comes_first(
    client: TestClient, settings: Settings, signed_post: SignedPost, device_ping: PingBuilder
) -> None:
    kitchen = "a0:b7:65:12:34:56"
    bathroom = "a0:b7:65:aa:bb:cc"
    for address in (kitchen, bathroom):
        assert signed_post(f"/api/v1/devices/{address}/ping", device_ping()).status_code == 200
    last_seen(settings, bathroom, datetime.now(UTC) - timedelta(hours=2))

    listed = client.get("/api/v1/devices").json()

    assert listed["total"] == 2
    assert [item["address"] for item in listed["items"]] == [kitchen, bathroom]


def test_given_a_gateway_pinging_from_an_address_written_differently_when_asked_then_it_is_one_device(
    client: TestClient, signed_post: SignedPost, device_ping: PingBuilder
) -> None:
    assert signed_post("/api/v1/devices/A0:B7:65:12:34:56/ping", device_ping()).status_code == 200
    assert signed_post("/api/v1/devices/a0-b7-65-12-34-56/ping", device_ping()).status_code == 200

    listed = client.get("/api/v1/devices").json()

    assert listed["total"] == 1
    assert listed["items"][0]["address"] == GATEWAY_ADDRESS


def test_given_a_gateway_told_to_listen_for_an_address_tail_when_it_pings_then_the_tail_is_kept(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """A gateway may be provisioned with the five octets the advertisement
    carries rather than the whole address, and the ping reports what it was told
    — that setting is the point of the field."""
    assert ping(device_ping(scale_addresses=["B8:D0:12:34:56"])).status_code == 200

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["scale_addresses"] == ["b8:d0:12:34:56"]


def test_given_a_gateway_listening_for_several_scales_when_it_pings_then_all_of_them_are_kept(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """One gateway serves every scale in radio range of it, and which ones those
    are is the whole of what this field says."""
    watched = ["08:B8:D0:12:34:56", "AA:BB:CC:DD:EE:FF"]
    assert ping(device_ping(scale_addresses=watched)).status_code == 200

    device = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()
    recorded = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings").json()["items"][0]

    assert device["scale_addresses"] == ["08:b8:d0:12:34:56", "aa:bb:cc:dd:ee:ff"]
    assert recorded["scale_addresses"] == device["scale_addresses"]


def test_given_a_gateway_too_old_to_send_a_list_when_it_pings_then_its_one_scale_is_kept(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """A board still running firmware that listens for a single scale pings on
    its own cadence, and going quiet here would read as the very failure this
    field exists to reveal."""
    body = device_ping()
    del body["scale_addresses"]
    body["scale_address"] = "08:B8:D0:12:34:56"

    assert ping(body).status_code == 200

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["scale_addresses"] == ["08:b8:d0:12:34:56"]


def test_given_a_gateway_with_no_scale_stored_when_it_pings_then_it_still_reports(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """A board provisioned with no scale is exactly the board worth seeing here,
    so an absent setting must not cost the ping."""
    assert ping(device_ping(scale_addresses=[])).status_code == 200

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["scale_addresses"] == []


def test_given_a_ping_when_recorded_then_the_address_it_arrived_from_is_kept(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """The gateway cannot know the address its traffic leaves the network with,
    so the server is the only thing that can write this down."""
    ping(device_ping())

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["client_ip"]


def test_given_an_unsigned_ping_when_posted_then_it_is_refused(client: TestClient, device_ping: PingBuilder) -> None:
    response = client.post(f"/api/v1/devices/{GATEWAY_ADDRESS}/ping", json=device_ping())

    assert response.status_code == 401
    assert client.get("/api/v1/devices").json()["total"] == 0


def test_given_a_ping_missing_what_it_reports_when_posted_then_it_is_rejected(
    ping: Delivery, device_ping: PingBuilder
) -> None:
    body = device_ping()
    del body["uptime_ms"]

    assert ping(body).status_code == 422


def test_given_a_ping_with_an_unknown_field_when_posted_then_it_is_rejected(
    ping: Delivery, device_ping: PingBuilder
) -> None:
    assert ping(device_ping(battery=90)).status_code == 422


def test_given_an_address_that_is_not_one_when_pinged_then_it_is_rejected(
    signed_post: SignedPost, device_ping: PingBuilder
) -> None:
    assert signed_post("/api/v1/devices/nonsense/ping", device_ping()).status_code == 422


def test_given_a_gateway_that_has_never_pinged_when_asked_for_then_it_is_not_found(client: TestClient) -> None:
    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").status_code == 404


def test_given_a_gateway_that_counts_its_boots_when_it_pings_then_the_tally_is_kept(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """The reset reason names the last boot only. The tally is what says how
    often a marginal supply has actually taken the board down."""
    device = ping(device_ping(reset_reason="brownout", boots=41, brownouts=7)).json()

    assert device["boots"] == 41
    assert device["brownouts"] == 7
    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["brownouts"] == 7


def test_given_a_gateway_too_old_to_count_boots_when_it_pings_then_the_tally_reads_zero(
    ping: Delivery, device_ping: PingBuilder
) -> None:
    device = ping(device_ping()).json()

    assert device["boots"] == 0
    assert device["brownouts"] == 0


def test_given_more_brownouts_than_boots_when_pinged_then_the_body_is_rejected(
    ping: Delivery, device_ping: PingBuilder
) -> None:
    """Every brownout is itself a boot, so a count that outnumbers them is a
    board that has miscounted rather than a board in trouble."""
    assert ping(device_ping(boots=3, brownouts=4)).status_code == 422
    assert ping(device_ping(boots=3, brownouts=-1)).status_code == 422


def test_given_a_gateway_that_has_pinged_when_it_is_renamed_then_the_name_sticks(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping())

    response = client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": "bathroom"})

    assert response.status_code == 200
    assert response.json()["name"] == "bathroom"
    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["name"] == "bathroom"


def test_given_a_named_gateway_when_the_name_is_cleared_then_it_is_nameless_again(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping())
    client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": "bathroom"})

    assert client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": None}).json()["name"] is None
    assert client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": "   "}).json()["name"] is None


def test_given_a_renamed_gateway_when_it_pings_then_the_new_name_survives(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping())
    client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": "bathroom"})

    assert ping(device_ping(queued=2)).json()["name"] == "bathroom"


def test_given_a_gateway_that_has_never_pinged_when_it_is_renamed_then_it_is_not_found(
    client: TestClient,
) -> None:
    """A row exists because a board reported for itself, so there is nothing to
    name until one has."""
    assert client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": "bathroom"}).status_code == 404


def test_given_a_name_longer_than_the_column_holds_when_renaming_then_it_is_rejected(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping())

    assert client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={"name": "b" * 65}).status_code == 422
    assert client.put(f"/api/v1/devices/{GATEWAY_ADDRESS}", json={}).status_code == 422


def test_given_a_gateway_that_reports_its_heap_when_it_pings_then_both_numbers_are_kept(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """A heap can be roomy and still unusable, so the pair is what says whether
    the next certificate chain has somewhere contiguous to be verified in."""
    device = ping(device_ping(free_heap=104_000, largest_free_block=51_200)).json()

    assert device["free_heap"] == 104_000
    assert device["largest_free_block"] == 51_200
    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}").json()["largest_free_block"] == 51_200


def test_given_a_gateway_too_old_to_report_its_heap_when_it_pings_then_it_says_nothing(
    ping: Delivery, device_ping: PingBuilder
) -> None:
    """No running board has a heap of zero, so a gateway that did not measure one
    has to be told apart from a gateway that has none left."""
    device = ping(device_ping()).json()

    assert device["free_heap"] is None
    assert device["largest_free_block"] is None


def test_given_a_largest_block_bigger_than_the_heap_when_pinged_then_the_body_is_rejected(
    ping: Delivery, device_ping: PingBuilder
) -> None:
    """A contiguous run of free memory is part of the free memory, so a pair the
    wrong way round means the board measured something these names do not name."""
    assert ping(device_ping(free_heap=40_000, largest_free_block=41_000)).status_code == 422
    assert ping(device_ping(free_heap=-1, largest_free_block=0)).status_code == 422


def test_given_several_pings_when_the_history_is_read_then_every_one_is_there(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """The device row keeps only the newest ping. A supply that fails now and
    then is invisible in any one of them, so the run of them is what is kept."""
    for uptime in (60_000, 120_000, 180_000):
        ping(device_ping(uptime_ms=uptime))

    history = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings").json()

    assert history["total"] == 3
    # Newest first, which is the order a board's last hour is read in.
    assert [item["uptime_ms"] for item in history["items"]] == [180_000, 120_000, 60_000]
    # And the board is still one board.
    assert client.get("/api/v1/devices").json()["total"] == 1


def test_given_a_ping_carrying_what_a_board_knows_when_it_is_read_back_then_nothing_is_lost(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    reported = device_ping(
        free_heap=64_048,
        largest_free_block=57_332,
        min_free_heap=51_004,
        previous_uptime_ms=1_207_000,
        wifi_disconnects=4,
        wifi_disconnect_reason=200,
        cpu_mhz=80,
        xtal_mhz=40,
    )

    device = ping(reported).json()
    recorded = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings").json()["items"][0]

    for field, value in reported.items():
        if field == "scale_addresses":
            # Stored the one spelling, whatever spelling arrived.
            continue
        assert device[field] == value, field
        assert recorded[field] == value, field


def test_given_a_board_too_old_to_report_the_new_fields_when_it_pings_then_it_is_still_recorded(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    """A board is reflashed when somebody gets to it, so the server meets both
    firmwares. What an older one cannot say reads as absent rather than as zero,
    except the counts, where nothing counted and none seen are the same thing."""
    response = ping(device_ping())

    assert response.status_code == 200
    device = response.json()
    assert device["min_free_heap"] is None
    assert device["previous_uptime_ms"] is None
    assert device["wifi_disconnects"] == 0


def test_given_a_long_history_when_it_is_paged_then_the_pages_do_not_overlap(
    client: TestClient, ping: Delivery, device_ping: PingBuilder
) -> None:
    for uptime in range(1, 6):
        ping(device_ping(uptime_ms=uptime * 60_000))

    first = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings?limit=2").json()
    second = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings?limit=2&offset=2").json()

    assert first["total"] == second["total"] == 5
    assert [item["uptime_ms"] for item in first["items"]] == [300_000, 240_000]
    assert [item["uptime_ms"] for item in second["items"]] == [180_000, 120_000]


def test_given_a_board_nobody_has_heard_from_when_its_history_is_read_then_it_is_empty(
    client: TestClient,
) -> None:
    """Empty rather than absent: a board with no pings and a board that does not
    exist are the same silence, and neither is an error to ask about."""
    history = client.get("/api/v1/devices/a0:b7:65:99:99:99/pings").json()

    assert history["total"] == 0
    assert history["items"] == []


def test_given_a_ping_older_than_the_history_is_kept_for_when_the_board_pings_again_then_it_is_dropped(
    client: TestClient, settings: Settings, ping: Delivery, device_ping: PingBuilder
) -> None:
    """Nothing sweeps the table on a timer: the ping that grows a board's history
    is the one that trims it."""
    ping(device_ping(uptime_ms=60_000))
    recorded_at(settings, GATEWAY_ADDRESS, datetime.now(UTC) - timedelta(days=8))

    ping(device_ping(uptime_ms=120_000))

    history = client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings").json()
    assert history["total"] == 1
    assert history["items"][0]["uptime_ms"] == 120_000


def test_given_a_ping_inside_the_history_when_the_board_pings_again_then_both_are_there(
    client: TestClient, settings: Settings, ping: Delivery, device_ping: PingBuilder
) -> None:
    ping(device_ping(uptime_ms=60_000))
    recorded_at(settings, GATEWAY_ADDRESS, datetime.now(UTC) - timedelta(days=6))

    ping(device_ping(uptime_ms=120_000))

    assert client.get(f"/api/v1/devices/{GATEWAY_ADDRESS}/pings").json()["total"] == 2


def test_given_a_board_that_has_stopped_when_another_board_pings_then_its_history_is_left_alone(
    client: TestClient, settings: Settings, signed_post: SignedPost, device_ping: PingBuilder
) -> None:
    """A board pays only for its own history. What a board that went quiet said
    before it did is the run of pings that ends where the board did, and no
    other board's ping is a reason to lose it."""
    stopped = "a0:b7:65:99:99:99"
    signed_post(f"/api/v1/devices/{stopped}/ping", device_ping())
    recorded_at(settings, stopped, datetime.now(UTC) - timedelta(days=30))

    signed_post(f"/api/v1/devices/{GATEWAY_ADDRESS}/ping", device_ping())

    assert client.get(f"/api/v1/devices/{stopped}/pings").json()["total"] == 1
