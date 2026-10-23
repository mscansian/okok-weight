import re

from fastapi.testclient import TestClient

from scale_server import web


def test_given_the_root_when_asked_for_then_the_page_is_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # The page is inert without the component the script registers, so its
    # absence is the difference between a page and a blank screen.
    assert 'x-data="weight"' in response.text


def test_given_the_gateways_page_when_asked_for_then_it_is_served(client: TestClient) -> None:
    response = client.get("/devices")

    assert response.status_code == 200
    assert 'x-data="gateways"' in response.text


def test_given_a_page_at_devices_when_the_api_route_is_asked_for_then_it_still_answers(
    client: TestClient,
) -> None:
    """Two things are called devices now, and only one of them is the API."""
    assert client.get("/api/v1/devices").json() == {"items": [], "total": 0}
    assert "text/html" in client.get("/devices").headers["content-type"]


def test_given_the_pages_when_their_files_are_asked_for_then_they_are_served(client: TestClient) -> None:
    """The pages carry no CDN, so anything they need has to be served from here."""
    for asset in (
        "/static/format.js",
        "/static/weight.js",
        "/static/devices.js",
        "/static/style.css",
        "/static/vendor/alpine.min.js",
    ):
        assert client.get(asset).status_code == 200, asset


def test_given_a_page_at_the_root_when_the_api_is_asked_for_then_it_still_answers(client: TestClient) -> None:
    """The page sits at `/` and its files under `/static`, so neither can end up
    in front of the routes a gateway posts to."""
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/docs").status_code == 200


def test_given_the_page_when_the_schema_is_read_then_only_the_api_is_in_it(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert paths
    assert all(path.startswith("/api/v1/") for path in paths), paths


def test_given_a_page_when_it_asks_for_its_files_then_the_urls_carry_a_version(client: TestClient) -> None:
    """A cache in front of the server keeps a file for as long as the freshness
    it was served with says to, and asks for it by url — so a deploy has to
    change the url rather than only the file behind it."""
    page = client.get("/").text
    versions = set(re.findall(r"/static/[\w./-]+\?v=(\w+)", page))

    assert len(versions) == 1, versions
    assert versions != {web.ASSET_TOKEN}

    asked_for = re.findall(r'(?:href|src)="(/static/[\w./-]+\?v=\w+)"', page)
    assert len(asked_for) == 4
    for url in asked_for:
        assert client.get(url).status_code == 200, url


def test_given_both_pages_when_served_then_they_name_the_same_version(client: TestClient) -> None:
    for path in ("/", "/devices"):
        assert web.ASSET_VERSION in client.get(path).text, path


def test_given_a_page_when_served_then_it_is_not_kept_by_a_cache(client: TestClient) -> None:
    """A held page would go on asking for the files it was written against,
    which is the whole of what the version above buys."""
    for path in ("/", "/devices"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_given_the_files_change_when_the_version_is_taken_again_then_it_differs() -> None:
    """The version has to follow the bytes, or a deploy that changes a file
    leaves every browser asking for the url it already has."""
    original = web._asset_version()
    scratch = web.STATIC_DIR / "scratch.css"
    scratch.write_text("/* not shipped */")
    try:
        assert web._asset_version() != original
    finally:
        scratch.unlink()

    assert web._asset_version() == original


def test_given_one_boards_page_when_asked_for_then_it_is_served(client: TestClient) -> None:
    response = client.get("/devices/a0:b7:65:12:34:56")

    assert response.status_code == 200
    assert 'x-data="gateway"' in response.text


def test_given_a_path_that_is_not_an_address_when_asked_for_then_it_is_not_a_board(
    client: TestClient,
) -> None:
    """The page reads the address out of its own url, so a url that carries
    something else would leave it asking the API about nonsense."""
    assert client.get("/devices/not-a-mac").status_code == 422


def test_given_a_board_page_when_the_api_route_is_asked_for_then_it_still_answers(
    client: TestClient,
) -> None:
    """Three things are called devices now, and only two of them are the API."""
    assert client.get("/api/v1/devices/a0:b7:65:12:34:56/pings").json()["total"] == 0
    assert "text/html" in client.get("/devices/a0:b7:65:12:34:56").headers["content-type"]
