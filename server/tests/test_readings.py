from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import Delivery, WeighInBuilder


def test_given_a_weigh_in_when_posted_then_it_is_stored_and_returned(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    body = weigh_in(72.35)

    response = post(body)

    assert response.status_code == 201
    created = response.json()
    assert created["weight_kg"] == 72.35
    assert created["id"]
    assert created["event_id"] == body["event_id"]
    assert created["measured_at"] == created["recorded_at"]

    listed = client.get("/api/v1/readings").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == created["id"]


def test_given_a_weigh_in_already_stored_when_it_is_delivered_again_then_it_stays_one_reading(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    body = weigh_in(72.35)
    first = post(body)

    repeat = post(body)

    assert first.status_code == 201
    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    assert client.get("/api/v1/readings").json()["total"] == 1


def test_given_two_weigh_ins_of_the_same_weight_when_posted_then_both_are_stored(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    assert post(weigh_in(72.35)).status_code == 201
    assert post(weigh_in(72.35)).status_code == 201

    assert client.get("/api/v1/readings").json()["total"] == 2


def test_given_a_body_without_an_event_id_when_posted_then_it_is_rejected(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    body = weigh_in(72.35)
    del body["event_id"]

    assert post(body).status_code == 422


def test_given_a_gateway_ping_when_posted_to_the_readings_route_then_it_is_rejected(post: Delivery) -> None:
    """A ping is a device reporting on itself, and this route takes weigh-ins.
    It used to take both, and a gateway still sending one here is one whose
    firmware never caught up."""
    assert post({"ping": True}).status_code == 422


def test_given_several_weigh_ins_when_listed_then_the_most_recent_comes_first(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    for weight in (70.0, 71.0, 72.0):
        assert post(weigh_in(weight)).status_code == 201

    listed = client.get("/api/v1/readings").json()

    assert [item["weight_kg"] for item in listed["items"]] == [72.0, 71.0, 70.0]


def test_given_more_weigh_ins_than_a_page_when_paged_then_pages_do_not_overlap(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    for weight in range(60, 70):
        post(weigh_in(float(weight)))

    first = client.get("/api/v1/readings", params={"limit": 4}).json()
    second = client.get("/api/v1/readings", params={"limit": 4, "offset": 4}).json()

    assert first["total"] == second["total"] == 10
    assert len(first["items"]) == len(second["items"]) == 4
    assert {item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in second["items"])


def test_given_a_page_size_above_the_maximum_when_listed_then_it_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/readings", params={"limit": 101}).status_code == 422


def test_given_an_unknown_field_when_posted_then_the_body_is_rejected(post: Delivery, weigh_in: WeighInBuilder) -> None:
    body = weigh_in(72.0) | {"battery": 90}

    assert post(body).status_code == 422


def test_given_an_impossible_weight_when_posted_then_the_body_is_rejected(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    assert post(weigh_in(0.0)).status_code == 422
    assert post(weigh_in(900.0)).status_code == 422


def test_given_readings_stored_when_health_is_asked_then_it_counts_them(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    post(weigh_in(72.0))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "readings": 1}


def test_given_the_gateway_could_not_measure_the_signal_when_posted_then_the_weigh_in_still_lands(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    # 127 is the Bluetooth spec's "RSSI not available", and the controller is
    # entitled to report it. The weight is what matters, so it must survive.
    body = weigh_in(72.35) | {"rssi": 127}

    assert post(body).status_code == 201
    assert client.get("/api/v1/readings").json()["total"] == 1


def test_given_several_scales_reporting_when_listed_by_address_then_only_that_scale_comes_back(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    kitchen = "08:b8:d0:12:34:56"
    bathroom = "08:b8:d0:12:34:57"
    for address, weight in ((kitchen, 72.0), (bathroom, 81.0), (kitchen, 72.5)):
        assert post(weigh_in(weight) | {"address": address}).status_code == 201

    listed = client.get("/api/v1/readings", params={"address": kitchen}).json()

    assert listed["total"] == 2
    assert [item["weight_kg"] for item in listed["items"]] == [72.5, 72.0]
    assert {item["address"] for item in listed["items"]} == {kitchen}
    assert client.get("/api/v1/readings").json()["total"] == 3


def test_given_an_address_written_differently_when_posted_then_it_groups_with_the_same_scale(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    post(weigh_in(72.0) | {"address": "08:B8:D0:12:34:56"})
    post(weigh_in(72.5) | {"address": "08-b8-d0-12-34-56"})

    listed = client.get("/api/v1/readings", params={"address": "08:b8:d0:12:34:56"}).json()

    assert listed["total"] == 2
    assert {item["address"] for item in listed["items"]} == {"08:b8:d0:12:34:56"}


def test_given_an_address_that_is_not_one_when_listed_then_it_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/readings", params={"address": "nonsense"}).status_code == 422


def test_given_a_weigh_in_the_gateway_held_back_when_posted_then_it_is_timed_to_when_it_was_taken(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    held_for = timedelta(hours=3)
    body = weigh_in(72.35) | {"age_ms": int(held_for.total_seconds() * 1000)}

    response = post(body)

    assert response.status_code == 201
    created = response.json()
    recorded_at = datetime.fromisoformat(created["recorded_at"])
    measured_at = datetime.fromisoformat(created["measured_at"])
    # Arrival is now, so the weigh-in sits three hours behind it, give or take
    # however long the request took.
    assert timedelta(seconds=-5) < (recorded_at - measured_at) - held_for < timedelta(seconds=5)


def test_given_a_weigh_in_held_back_when_listed_then_it_sorts_by_when_it_was_taken(
    client: TestClient, post: Delivery, weigh_in: WeighInBuilder
) -> None:
    """A gateway coming back from an outage delivers older weigh-ins after newer
    ones it never had to hold. The history is about the scale, not the link, so
    the late arrival has to land in its own place in it."""
    yesterday = weigh_in(70.0) | {"age_ms": 20 * 60 * 60 * 1000}
    an_hour_ago = weigh_in(71.0) | {"age_ms": 60 * 60 * 1000}
    just_now = weigh_in(72.0)

    for body in (just_now, yesterday, an_hour_ago):
        assert post(body).status_code == 201

    listed = client.get("/api/v1/readings").json()

    assert [item["weight_kg"] for item in listed["items"]] == [72.0, 71.0, 70.0]


def test_given_a_weigh_in_without_an_age_when_posted_then_it_is_timed_by_its_arrival(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    """A gateway that posts as it reads sends no age, and there is nothing to
    subtract from its arrival."""
    response = post(weigh_in(72.35))

    created = response.json()
    assert created["measured_at"] == created["recorded_at"]


def test_given_an_age_that_is_not_a_delay_when_posted_then_the_body_is_rejected(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    # A weigh-in cannot have been taken after it arrived, and no gateway holds
    # one for a fortnight.
    assert post(weigh_in(72.0) | {"age_ms": -1}).status_code == 422
    assert post(weigh_in(72.0) | {"age_ms": 14 * 24 * 60 * 60 * 1000}).status_code == 422


def test_given_a_weigh_in_reporting_its_locks_when_posted_then_it_lands(
    post: Delivery, weigh_in: WeighInBuilder
) -> None:
    """The count describes how well the gateway heard the scale, so it is logged
    rather than stored — but the body still has to be one the server accepts, or
    a gateway that counts locks would lose every weigh-in it took."""
    assert post(weigh_in(72.35) | {"locks": 18}).status_code == 201
    assert post(weigh_in(71.0)).status_code == 201
    assert post(weigh_in(70.0) | {"locks": -1}).status_code == 422
