"""The API the scale gateway posts to and the weigh-in history is read from."""

import logging
import logging.config
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from scale_server import database, devices, logs, middleware, readings, web
from scale_server.config import Settings, load_settings
from scale_server.middleware import client_ip
from scale_server.models import (
    BLUETOOTH_ADDRESS,
    Device,
    DeviceList,
    DeviceName,
    DevicePing,
    DevicePingPage,
    Health,
    Reading,
    ReadingPage,
    WeighIn,
    canonical_address,
)
from scale_server.signatures import verify_signature

# Every route lives under one versioned prefix, so a later shape of this API can
# be served beside this one rather than in place of it — which matters here
# because the clients are boards on a shelf, reflashed one at a time. The gateway
# signs the path it posts to, so both sides spell this the same way.
API_PREFIX = "/api/v1"

# Every route that takes one takes it the same way, and refuses anything that is
# not an address before the handler ever sees it.
DeviceAddress = Annotated[str, Path(pattern=BLUETOOTH_ADDRESS)]

log = logging.getLogger(__name__)


def open_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request. WAL and a busy timeout carry the concurrency."""
    connection = database.connect(request.app.state.settings.db_path)
    try:
        yield connection
    finally:
        connection.close()


Connection = Annotated[sqlite3.Connection, Depends(open_connection)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.migrate(settings.db_path)
        log.info("server ready", extra={"db_path": str(settings.db_path)})
        yield

    app = FastAPI(
        title="okok-weight",
        summary="Receives weigh-ins from the scale gateway and stores them.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Added innermost first, so a request meets the request id, then the rate
    # limit, then the size check — every rejection is logged under an id.
    app.add_middleware(middleware.BodySizeLimit, max_bytes=settings.max_body_bytes)
    app.add_middleware(middleware.RateLimit, per_minute=settings.rate_limit_per_minute)
    app.add_middleware(middleware.RequestContext)

    @app.exception_handler(RequestValidationError)
    async def report_rejected_body(request: Request, error: RequestValidationError) -> JSONResponse:
        """The gateway is headless, so a body it cannot get right is only visible here."""
        # A validator that refuses a whole model reports the ValueError it raised
        # among the details, and that object is not JSON. Encoding covers both
        # the response and the log line, which are one JSON document each.
        reasons = jsonable_encoder(error.errors())
        log.warning("request body rejected", extra={"path": request.url.path, "errors": reasons})
        return JSONResponse({"detail": reasons}, status_code=422)

    api = APIRouter(prefix=API_PREFIX)

    @api.post("/readings", status_code=201, dependencies=[Depends(verify_signature)])
    def record_reading(body: WeighIn, response: Response, connection: Connection) -> Reading:
        """Takes one weigh-in from a gateway."""
        reading, created = readings.record(connection, body.event_id, body.address, body.weight_kg, body.age_ms)
        if not created:
            # The gateway repeating itself is an expected input, not a failure:
            # it gets back the weigh-in its first delivery produced.
            response.status_code = 200
        log.info(
            "weigh-in recorded" if created else "weigh-in already recorded",
            extra={
                "reading_id": str(reading.id),
                "event_id": str(body.event_id),
                "weight_kg": reading.weight_kg,
                "field_b": body.field_b,
                "sequence": body.sequence,
                "rssi": body.rssi,
                "scale_address": body.address,
                # Non-zero says the gateway held this one back, which is the
                # only trace an outage at the gateway leaves here.
                "age_ms": body.age_ms,
                # A count near one is a gateway that heard the end of this
                # weigh-in only just in time.
                "locks": body.locks,
            },
        )
        return reading

    @api.get("/readings")
    def list_readings(
        connection: Connection,
        address: Annotated[str | None, Query(pattern=BLUETOOTH_ADDRESS)] = None,
        limit: Annotated[int, Query(ge=1, le=settings.max_page_size)] = settings.default_page_size,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ReadingPage:
        """Weigh-ins, most recent first, from one scale or from every scale that
        has ever reported here."""
        scale = None if address is None else canonical_address(address)
        return ReadingPage(
            items=readings.page(connection, limit, offset, scale),
            total=readings.count(connection, scale),
            limit=limit,
            offset=offset,
        )

    @api.post("/devices/{address}/ping", dependencies=[Depends(verify_signature)])
    def record_device_ping(
        address: DeviceAddress, body: DevicePing, request: Request, connection: Connection
    ) -> Device:
        """Takes a gateway's report that it is alive, and how it is getting on.

        Answering with the whole device rather than an acknowledgement is what
        makes a ping sent by hand worth reading: it comes back saying what the
        server now believes about the board that sent it.
        """
        device = devices.record_ping(
            connection,
            canonical_address(address),
            body,
            client_ip(request),
            settings.device_offline_after_pings,
            settings.device_ping_history_days,
        )
        log.info(
            "device ping",
            extra={
                "device_address": device.address,
                "scale_addresses": device.scale_addresses,
                "ssid": device.ssid,
                "wifi_rssi": device.wifi_rssi,
                # Non-zero says weigh-ins exist that this history has not got.
                "queued": device.queued,
                "uptime_ms": device.uptime_ms,
                "firmware_version": device.firmware_version,
                "reset_reason": device.reset_reason,
                # The reason names the last boot; these two say how many there
                # have been, which is what makes a marginal supply a rate.
                "boots": device.boots,
                "brownouts": device.brownouts,
                # The chain this board verifies to reach us is multiplied out of
                # bignums, so the largest block is what a gateway runs out of
                # first and the total alone would not show it going.
                "free_heap": device.free_heap,
                "largest_free_block": device.largest_free_block,
                # The floor the heap reached, which the reading above cannot
                # show once the pressure that caused it has passed.
                "min_free_heap": device.min_free_heap,
                # How long the run before this one lasted, to the second rather
                # than to the nearest ping.
                "previous_uptime_ms": device.previous_uptime_ms,
                # Link churn, which costs a ping and a handshake per drop.
                "wifi_disconnects": device.wifi_disconnects,
            },
        )
        return device

    @api.get("/devices")
    def list_devices(connection: Connection) -> DeviceList:
        """Every gateway that has ever pinged, the one heard from most recently first."""
        found = devices.listing(connection, settings.device_offline_after_pings)
        return DeviceList(items=found, total=len(found))

    @api.get("/devices/{address}/pings")
    def list_device_pings(
        address: DeviceAddress,
        connection: Connection,
        limit: Annotated[int, Query(ge=1, le=settings.max_page_size)] = settings.default_page_size,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DevicePingPage:
        """Everything one board has said about itself, most recent first.

        The device row carries only the latest of these. A supply that fails now
        and then is not visible in any one ping, so the run of them is what there
        is to read.
        """
        board = canonical_address(address)
        return DevicePingPage(
            items=devices.ping_history(connection, board, limit, offset),
            total=devices.ping_count(connection, board),
            limit=limit,
            offset=offset,
        )

    @api.get("/devices/{address}")
    def get_device(address: DeviceAddress, connection: Connection) -> Device:
        device = devices.find(connection, canonical_address(address), settings.device_offline_after_pings)
        if device is None:
            raise HTTPException(status_code=404, detail="no such device")
        return device

    @api.put("/devices/{address}")
    def rename_device(address: DeviceAddress, body: DeviceName, connection: Connection) -> Device:
        """Sets what a board is called.

        The name is the whole of what there is to edit here: every other column
        is the board's own report of itself, which a ping rewrites.
        """
        device = devices.rename(connection, canonical_address(address), body.name, settings.device_offline_after_pings)
        if device is None:
            raise HTTPException(status_code=404, detail="no such device")
        log.info("device renamed", extra={"device_address": device.address, "device_name": device.name})
        return device

    @api.get("/health")
    def health(connection: Connection) -> Health:
        """Answers only once the database has answered, so it fails when storage does."""
        return Health(status="ok", readings=readings.count(connection))

    app.include_router(api)

    # The page and its files sit outside the versioned prefix: they are the
    # server's own front door rather than part of the API a gateway speaks to.
    app.include_router(web.router)
    app.mount("/static", web.assets, name="static")
    return app


def run() -> None:
    settings = load_settings()
    log_config = logs.logging_config(settings.log_level)
    logging.config.dictConfig(log_config)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=log_config,
        access_log=False,
    )
