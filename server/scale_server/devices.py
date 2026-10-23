"""The queries the devices table exists for.

A device is one gateway, and it writes its own row: the first ping a board ever
sends makes the row, and every ping after it rewrites what the board reported.
Nothing has to register a gateway first, because the signing key is what says a
ping is genuine, and a list of boards to admit would be one more thing to keep in
step with the shelf.

`name` and `first_seen_at` are the two columns a ping leaves alone. One belongs
to whoever put the board somewhere, and the other to the ping that came first.

Every ping is also kept whole in `device_pings`. The row above answers what a
board is doing now; the history answers what it was doing in the hour before it
stopped, which for a board that fails and comes back is where the failure is.
That history reaches back a set number of days and no further, and each board
prunes its own as it pings.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from scale_server.database import TIMESTAMP_FORMAT, transaction
from scale_server.models import Device, DevicePing, DevicePingRecord

# What the board says about itself, named once. Every statement here builds its
# column list from this, and the names match the model's fields, so adding a
# field to a ping is one edit rather than four that have to agree.
_REPORTED = (
    "scale_addresses",
    "ssid",
    "wifi_rssi",
    "queued",
    "uptime_ms",
    "interval_ms",
    "firmware_version",
    "reset_reason",
    "boots",
    "brownouts",
    "free_heap",
    "largest_free_block",
    "min_free_heap",
    "previous_uptime_ms",
    "wifi_disconnects",
    "wifi_disconnect_reason",
    "cpu_mhz",
    "xtal_mhz",
)

# What the server adds to what the board sent.
_OBSERVED = ("client_ip", "last_seen_at")

_COLUMNS = ", ".join(("address", "name", *_REPORTED, "client_ip", "first_seen_at", "last_seen_at"))

# address, then NULL for the name the board knows nothing about, then everything
# it reported, then client_ip and the two timestamps.
_PLACEHOLDERS = ", ".join(["?", "NULL", *["?"] * (len(_REPORTED) + 3)])

_PING_COLUMNS = ("recorded_at", "client_ip", *_REPORTED)


def _reported_values(ping: DevicePing) -> tuple[object, ...]:
    """What the board reported, in column order.

    The scales it watches are the one reported field that is a list, and SQLite
    has no column for a list, so it is stored as the JSON array it arrived as.
    These two functions are the only place that spelling exists.
    """
    reported = {column: getattr(ping, column) for column in _REPORTED}
    if reported["scale_addresses"] is not None:
        reported["scale_addresses"] = json.dumps(reported["scale_addresses"])
    return tuple(reported[column] for column in _REPORTED)


def _reported_fields(row: sqlite3.Row) -> dict[str, Any]:
    """What the board reported, as the models spell it."""
    reported = {column: row[column] for column in _REPORTED}
    if reported["scale_addresses"] is not None:
        reported["scale_addresses"] = json.loads(reported["scale_addresses"])
    return reported


def record_ping(
    connection: sqlite3.Connection,
    address: str,
    ping: DevicePing,
    client_ip: str,
    offline_after_pings: int,
    history_days: int,
) -> Device:
    """Writes down that this gateway was alive just now, and what it reported,
    and drops what this board said long enough ago to be past reading.

    The snapshot, the history and the pruning are written together, so a ping
    that reaches one reaches all three and they can never disagree about what
    arrived.
    """
    now = datetime.now(UTC)
    seen_at = now.strftime(TIMESTAMP_FORMAT)
    rewrites = ", ".join(f"{column} = excluded.{column}" for column in (*_REPORTED, *_OBSERVED))
    reported = _reported_values(ping)

    with transaction(connection):
        row = connection.execute(
            f"INSERT INTO devices ({_COLUMNS}) VALUES ({_PLACEHOLDERS}) "
            f"ON CONFLICT (address) DO UPDATE SET {rewrites} "
            f"RETURNING {_COLUMNS}",
            (address, *reported, client_ip, seen_at, seen_at),
        ).fetchone()

        history = ", ".join(("address", *_PING_COLUMNS))
        connection.execute(
            f"INSERT INTO device_pings ({history}) "
            f"VALUES ({', '.join(['?'] * (len(_PING_COLUMNS) + 1))})",
            (address, seen_at, client_ip, *reported),
        )

        # A board pays for its own history as it pings, which is why this is
        # here and not in something swept across the table on a timer: the row
        # that grows the history is the row that trims it, and the query is the
        # one the recency index was already built to serve.
        #
        # A board that has stopped pinging therefore keeps its last window
        # intact — which is the part worth keeping, because it is the run of
        # pings that ends where the board did.
        oldest_kept = (now - timedelta(days=history_days)).strftime(TIMESTAMP_FORMAT)
        connection.execute(
            "DELETE FROM device_pings WHERE address = ? AND recorded_at < ?", (address, oldest_kept)
        )

    return _from_row(row, now, offline_after_pings)


def find(connection: sqlite3.Connection, address: str, offline_after_pings: int) -> Device | None:
    row = connection.execute(f"SELECT {_COLUMNS} FROM devices WHERE address = ?", (address,)).fetchone()
    return None if row is None else _from_row(row, datetime.now(UTC), offline_after_pings)


def listing(connection: sqlite3.Connection, offline_after_pings: int) -> list[Device]:
    """Every gateway that has ever pinged, the one heard from most recently first.

    Unpaginated, and it can afford to be: a row here is a board somebody bought
    and plugged in, so the table is as long as the shelf it sits on.
    """
    now = datetime.now(UTC)
    rows = connection.execute(f"SELECT {_COLUMNS} FROM devices ORDER BY last_seen_at DESC, address").fetchall()
    return [_from_row(row, now, offline_after_pings) for row in rows]


def rename(connection: sqlite3.Connection, address: str, name: str | None, offline_after_pings: int) -> Device | None:
    """Sets what a gateway is called, or takes the name away.

    Only a ping brings a row into being, so there is nothing here to name until
    the board has spoken for itself at least once.
    """
    with transaction(connection):
        row = connection.execute(
            f"UPDATE devices SET name = ? WHERE address = ? RETURNING {_COLUMNS}", (name, address)
        ).fetchone()

    return None if row is None else _from_row(row, datetime.now(UTC), offline_after_pings)


def ping_count(connection: sqlite3.Connection, address: str) -> int:
    return connection.execute("SELECT COUNT(*) FROM device_pings WHERE address = ?", (address,)).fetchone()[0]


def ping_history(connection: sqlite3.Connection, address: str, limit: int, offset: int) -> list[DevicePingRecord]:
    """One board's pings, most recent first. The primary key breaks ties, so two
    pings inside the same clock tick still page without overlapping or skipping."""
    columns = ", ".join(_PING_COLUMNS)
    rows = connection.execute(
        f"SELECT {columns} FROM device_pings WHERE address = ? "
        f"ORDER BY recorded_at DESC, id DESC LIMIT ? OFFSET ?",
        (address, limit, offset),
    ).fetchall()
    return [
        DevicePingRecord(
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            client_ip=row["client_ip"],
            **_reported_fields(row),
        )
        for row in rows
    ]


def _is_online(last_seen_at: datetime, interval_ms: int, offline_after_pings: int, now: datetime) -> bool:
    """A gateway is online until it has been quiet for longer than the run of
    pings it is allowed to miss.

    The interval is the one the gateway declared rather than one held here, so a
    board given a new cadence is judged by that cadence from its next ping — the
    threshold cannot be left behind measuring against a number nobody sends.
    """
    return now - last_seen_at <= timedelta(milliseconds=interval_ms * offline_after_pings)


def _from_row(row: sqlite3.Row, now: datetime, offline_after_pings: int) -> Device:
    last_seen_at = datetime.fromisoformat(row["last_seen_at"])
    return Device(
        address=row["address"],
        name=row["name"],
        online=_is_online(last_seen_at, row["interval_ms"], offline_after_pings, now),
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=last_seen_at,
        client_ip=row["client_ip"],
        **_reported_fields(row),
    )
