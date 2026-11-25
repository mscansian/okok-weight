"""The tools an agent reads the weigh-in history and the gateways through.

Each result carries what the agent needs to answer, and nothing it would have to
skip past: no ids, no fields that are null, and every time with its offset.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from scale_server import database, devices, readings
from scale_server.config import Settings
from scale_server.models import BLUETOOTH_ADDRESS, Device, Reading, canonical_address

# Every tool here only reads.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

# A row is about 60 tokens, so the largest page stays near 12k.
DEFAULT_WEIGH_INS = 50
MAX_WEIGH_INS = 200

ScaleAddress = Annotated[
    str | None,
    Field(
        pattern=BLUETOOTH_ADDRESS,
        description="Only weigh-ins from this scale, as AA:BB:CC:DD:EE:FF. Leave out to include every scale.",
    ),
]


class AgentResult(BaseModel):
    """A result the agent reads. Null fields are left out instead of sent empty."""

    @model_serializer(mode="wrap")
    def without_nulls(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class WeighIn(AgentResult):
    measured_at: datetime
    weight_kg: float


class RangeSummary(AgentResult):
    count: int
    lowest_kg: float
    highest_kg: float
    average_kg: float
    earliest: WeighIn
    latest: WeighIn


class WeighInHistory(AgentResult):
    weigh_ins: list[WeighIn] = Field(description="Most recent first.")
    total: int = Field(description="How many weigh-ins the range holds, shown or not.")
    truncated: bool = Field(description="True when the oldest weigh-ins in the range were left out.")
    summary: RangeSummary | None = Field(default=None, description="Covers the whole range. Absent when it is empty.")


class DeviceRow(AgentResult):
    address: str
    name: str | None
    online: bool
    last_seen_at: datetime
    queued: int
    firmware_version: str
    scale_addresses: list[str] | None


class DeviceList(AgentResult):
    devices: list[DeviceRow]


class DeviceDetail(Device, AgentResult):
    pass


def register(server: MCPServer, settings: Settings) -> None:
    @contextmanager
    def connection() -> Iterator[sqlite3.Connection]:
        opened = database.connect(settings.db_path)
        try:
            yield opened
        finally:
            opened.close()

    @server.tool(annotations=READ_ONLY)
    def get_latest_weigh_in(scale_address: ScaleAddress = None) -> WeighIn | None:
        """The most recent weigh-in: the weight and when it was measured.

        Returns null when no weigh-in has been recorded. For a weight at an
        earlier time, or a trend, use list_weigh_ins.
        """
        scale = None if scale_address is None else canonical_address(scale_address)
        with connection() as opened:
            found = readings.page(opened, limit=1, offset=0, address=scale)
        return _weigh_in(found[0]) if found else None

    @server.tool(annotations=READ_ONLY)
    def list_weigh_ins(
        start: Annotated[AwareDatetime, Field(description="ISO 8601 datetime with an offset, included.")],
        end: Annotated[AwareDatetime, Field(description="ISO 8601 datetime with an offset, included.")],
        scale_address: ScaleAddress = None,
        limit: Annotated[int, Field(ge=1, le=MAX_WEIGH_INS)] = DEFAULT_WEIGH_INS,
    ) -> WeighInHistory:
        """Weigh-ins measured between start and end, most recent first.

        The summary covers every weigh-in in the range, so use it for questions
        like "how much did I lose since March" even when the list is truncated.
        When `truncated` is true, the oldest weigh-ins were left out: narrow the
        range to see them. Datetimes need an offset, for example
        2026-10-01T00:00:00-03:00.
        """
        if start > end:
            raise ToolError("start is after end.")

        scale = None if scale_address is None else canonical_address(scale_address)
        with connection() as opened:
            found = readings.in_range(opened, start, end, limit, scale)
            summary = readings.range_summary(opened, start, end, scale)

        total = 0 if summary is None else summary.count
        return WeighInHistory(
            weigh_ins=[_weigh_in(reading) for reading in found],
            total=total,
            truncated=total > len(found),
            summary=None
            if summary is None
            else RangeSummary(
                count=summary.count,
                lowest_kg=summary.lowest_kg,
                highest_kg=summary.highest_kg,
                average_kg=round(summary.average_kg, 2),
                earliest=_weigh_in(summary.earliest),
                latest=_weigh_in(summary.latest),
            ),
        )

    @server.tool(annotations=READ_ONLY)
    def list_devices() -> DeviceList:
        """Every gateway that has ever reported, the one heard from most recently first.

        A gateway is the board that listens to the scale and sends its weigh-ins
        here. `last_seen_at` is its last ping, and `online` is false once it has
        missed several pings. `queued` above zero means weigh-ins the gateway has
        not delivered yet. Use get_device for everything a gateway reports.
        """
        with connection() as opened:
            found = devices.listing(opened, settings.device_offline_after_pings)
        return DeviceList(devices=[DeviceRow.model_validate(device.model_dump()) for device in found])

    @server.tool(annotations=READ_ONLY)
    def get_device(
        address: Annotated[
            str, Field(pattern=BLUETOOTH_ADDRESS, description="The gateway's address, from list_devices.")
        ],
    ) -> DeviceDetail:
        """Everything one gateway last reported about itself.

        `last_seen_at` is its last ping. Fields ending in _ms are milliseconds,
        and the heap fields are bytes. `reset_reason` says why it last booted:
        a brownout is a power supply problem, a panic is a firmware problem.
        """
        with connection() as opened:
            found = devices.find(opened, canonical_address(address), settings.device_offline_after_pings)
        if found is None:
            raise ToolError("No gateway with that address has reported. list_devices shows the known ones.")
        return DeviceDetail.model_validate(found.model_dump())


def _weigh_in(reading: Reading) -> WeighIn:
    return WeighIn(measured_at=reading.measured_at, weight_kg=reading.weight_kg)
