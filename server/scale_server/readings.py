"""The queries the readings table exists for.

Two names differ between the model and the table. `event_id` is
`device_event_id` there, and `address` is `scale_address`: inside a request the
only event and the only address there are belong to the device that sent it,
while the table also holds an id of the server's own and could hold addresses of
other kinds. This module is the only thing that has to know both spellings.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from scale_server.database import TIMESTAMP_FORMAT, transaction
from scale_server.models import Reading

_COLUMNS = "id, device_event_id, scale_address, weight_kg, measured_at, recorded_at"


def record(
    connection: sqlite3.Connection, event_id: UUID, address: str, weight_kg: float, age_ms: int = 0
) -> tuple[Reading, bool]:
    """Stores one weigh-in and reports whether it was new.

    A gateway that repeats a delivery repeats the id it gave the weigh-in, which
    makes the repeat a request for the reading it already produced rather than a
    second trip to the scale.

    Neither the scale nor the gateway keeps a clock, so the weigh-in is timed by
    taking the age the gateway reported off its arrival here. A gateway posting
    as it reads reports no age and the two timestamps agree; one delivering a
    weigh-in it held over a dropped link puts `measured_at` back where the
    weigh-in happened while `recorded_at` stays when it finally arrived. Both
    come off one reading of the clock, so a slow insert cannot separate them.
    """
    now = datetime.now(UTC)
    measured_at = now - timedelta(milliseconds=age_ms)
    reading = Reading(
        id=uuid4(),
        event_id=event_id,
        address=address,
        weight_kg=weight_kg,
        measured_at=measured_at,
        recorded_at=now,
    )

    with transaction(connection):
        accepted = connection.execute(
            f"INSERT INTO readings ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (device_event_id) DO NOTHING RETURNING id",
            (
                str(reading.id),
                str(event_id),
                address,
                weight_kg,
                measured_at.strftime(TIMESTAMP_FORMAT),
                now.strftime(TIMESTAMP_FORMAT),
            ),
        ).fetchone()

        if accepted is None:
            already_stored = connection.execute(
                f"SELECT {_COLUMNS} FROM readings WHERE device_event_id = ?", (str(event_id),)
            ).fetchone()
            reading = _from_row(already_stored)

    return reading, accepted is not None


def count(connection: sqlite3.Connection, address: str | None = None) -> int:
    if address is None:
        return connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    return connection.execute("SELECT COUNT(*) FROM readings WHERE scale_address = ?", (address,)).fetchone()[0]


def page(connection: sqlite3.Connection, limit: int, offset: int, address: str | None = None) -> list[Reading]:
    """Most recent weigh-in first, from one scale or from all of them. The
    primary key breaks ties so pages never overlap or skip."""
    order = "ORDER BY measured_at DESC, id DESC LIMIT ? OFFSET ?"
    if address is None:
        rows = connection.execute(f"SELECT {_COLUMNS} FROM readings {order}", (limit, offset)).fetchall()
    else:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM readings WHERE scale_address = ? {order}", (address, limit, offset)
        ).fetchall()
    return [_from_row(row) for row in rows]


def _from_row(row: sqlite3.Row) -> Reading:
    return Reading(
        id=UUID(row["id"]),
        event_id=UUID(row["device_event_id"]),
        address=row["scale_address"],
        weight_kg=row["weight_kg"],
        measured_at=datetime.fromisoformat(row["measured_at"]),
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
    )
