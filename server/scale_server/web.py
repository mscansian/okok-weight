"""The pages a person opens, as against the API the gateway posts to.

Both pages are files. Everything on them is drawn by the browser from the same
JSON routes a curl would use, so there is nothing for the server to fill in
before they go out — and nothing on them that is not also available to whoever
would rather ask for the JSON.
"""

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Path as PathParam
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from scale_server.models import BLUETOOTH_ADDRESS

STATIC_DIR = Path(__file__).parent / "static"

# What the pages write into the urls they fetch their own files from.
ASSET_TOKEN = "__ASSETS__"


def _asset_version() -> str:
    """A name for this build of the stylesheet and scripts.

    A cache in front of this server keeps a file for as long as the freshness it
    was served with says to, and it is asked for by url — so changing a file has
    to change the url, or the old one is handed out until it expires and the
    change is invisible for as long as that takes.

    Read once, because the files cannot change under a running container. Editing
    one on a developer's machine therefore wants a restart, the same as editing
    the code beside it.
    """
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()

# Built here rather than where it is mounted, so a build that shipped without
# the pages says so at startup instead of on the first request for one.
assets = StaticFiles(directory=STATIC_DIR)

router = APIRouter()


def _page(name: str) -> HTMLResponse:
    html = (STATIC_DIR / name).read_text(encoding="utf-8").replace(ASSET_TOKEN, ASSET_VERSION)
    # The page is what carries the versioned urls, so a page held in a cache
    # would go on asking for the files it was written against.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# Kept out of the schema, which is about the API.
@router.get("/", include_in_schema=False)
def weight_page() -> HTMLResponse:
    """The weight monitor: the last weigh-in, the trend, the history."""
    return _page("index.html")


@router.get("/devices", include_in_schema=False)
def devices_page() -> HTMLResponse:
    """Everything the gateways last reported about themselves."""
    return _page("devices.html")


@router.get("/devices/{address}", include_in_schema=False)
def device_page(address: Annotated[str, PathParam(pattern=BLUETOOTH_ADDRESS)]) -> HTMLResponse:
    """One board, at length: the runs it has had, and every ping behind them.

    The address is in the path rather than a query, so a board under suspicion
    is a link somebody can send. The page reads it back out of the url — nothing
    here is filled in before the file goes out.
    """
    return _page("device.html")
