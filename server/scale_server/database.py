"""SQLite connection handling and the schema migrations that build the database."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

# Fixed-width UTC, so stored text sorts chronologically. Every timestamp column
# below carries a CHECK holding it to this shape, and every writer formats with
# this, so the two cannot drift apart.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# Append-only. Each entry is the list of statements that moves the schema from
# version N to N + 1, and `PRAGMA user_version` records how far a file has got.
MIGRATIONS: list[tuple[str, ...]] = [
    (
        """
        CREATE TABLE readings (
            id              TEXT NOT NULL PRIMARY KEY,
            -- The id the gateway gave this weigh-in, which it repeats when it
            -- repeats a delivery, so a repeat lands on the row the first
            -- delivery wrote instead of beside it.
            device_event_id TEXT NOT NULL UNIQUE,
            -- Which scale was stood on. The pattern holds the column to the one
            -- spelling of an address, so readings from one scale group together
            -- however the gateway wrote it.
            scale_address   TEXT NOT NULL CHECK (scale_address LIKE '__:__:__:__:__:__'),
            weight_kg       REAL NOT NULL CHECK (weight_kg > 0 AND weight_kg < 500),
            -- When the weigh-in happened, against when it reached the server.
            -- Readings are ordered by the former, and that ordering is
            -- chronological only while every value is a fixed-width UTC
            -- timestamp, which is what the patterns pin down.
            measured_at     TEXT NOT NULL CHECK (measured_at LIKE '____-__-__T__:__:__%Z'),
            recorded_at     TEXT NOT NULL CHECK (recorded_at LIKE '____-__-__T__:__:__%Z')
        )
        """,
        "CREATE INDEX readings_by_recency ON readings (measured_at DESC, id DESC)",
        # With several gateways posting here, one scale's history is the query
        # that matters, and it wants its own recency order.
        "CREATE INDEX readings_by_scale ON readings (scale_address, measured_at DESC, id DESC)",
    ),
    (
        """
        CREATE TABLE devices (
            -- A gateway, named by the address of its own radio: the one name it
            -- has that it was not given. That address is what the ping route
            -- carries, so a second key of the server's own would only be a
            -- second name for the same board.
            address          TEXT NOT NULL PRIMARY KEY CHECK (address LIKE '__:__:__:__:__:__'),
            -- Set here rather than by the board, which knows its own address and
            -- nothing whatever about where it was put.
            name             TEXT,
            -- Which scale this gateway was told to listen for. Five octets are
            -- as good as six: the advertisement carries only the tail, so a
            -- gateway provisioned with the tail alone recognises the scale.
            scale_address    TEXT CHECK (scale_address LIKE '__:__:__:__:__:__'
                                      OR scale_address LIKE '__:__:__:__:__'),
            ssid             TEXT,
            wifi_rssi        INTEGER,
            queued           INTEGER NOT NULL CHECK (queued >= 0),
            uptime_ms        INTEGER NOT NULL CHECK (uptime_ms >= 0),
            -- How often the gateway says it pings, which is what the length of
            -- its silence is measured against.
            interval_ms      INTEGER NOT NULL CHECK (interval_ms > 0),
            firmware_version TEXT NOT NULL,
            reset_reason     TEXT NOT NULL,
            -- Where the last ping came from, as the server saw it.
            client_ip        TEXT NOT NULL,
            first_seen_at    TEXT NOT NULL CHECK (first_seen_at LIKE '____-__-__T__:__:__%Z'),
            last_seen_at     TEXT NOT NULL CHECK (last_seen_at LIKE '____-__-__T__:__:__%Z')
        )
        """,
    ),
    (
        # What the board has counted for itself across its own reboots, which is
        # the one part of a ping that outlives the boot reporting it. Zero suits
        # the rows already here: they were written by gateways that counted
        # nothing, which is exactly what a gateway too old to count reports.
        "ALTER TABLE devices ADD COLUMN boots INTEGER NOT NULL DEFAULT 0 CHECK (boots >= 0)",
        "ALTER TABLE devices ADD COLUMN brownouts INTEGER NOT NULL DEFAULT 0 "
        "CHECK (brownouts >= 0 AND brownouts <= boots)",
    ),
    (
        # What the board had left to allocate when it built its last ping.
        # Reaching the server means verifying its certificate chain, which wants
        # contiguous room for bignums, so the largest block is the number that
        # runs out first and the total on its own would not show it going.
        #
        # Nullable, because a gateway too old to report them still pings and no
        # running board has a heap of zero.
        "ALTER TABLE devices ADD COLUMN free_heap INTEGER CHECK (free_heap >= 0)",
        (
            "ALTER TABLE devices ADD COLUMN largest_free_block INTEGER "
            "CHECK (largest_free_block >= 0 AND largest_free_block <= free_heap)"
        ),
    ),
    (
        # Every ping kept, rather than each one overwriting the last. The devices
        # row answers what a board is doing; only a history answers what it was
        # doing in the hour before it stopped, and for a board that fails now and
        # then and comes back that hour is the whole of the evidence.
        #
        # A board pings on the cadence it declares, so this grows at whatever
        # rate it chose. Five minutes is some three hundred rows a day, and a row
        # here is small enough that a year of one board is a few megabytes.
        """
        CREATE TABLE device_pings (
            id                     INTEGER PRIMARY KEY,
            address                TEXT NOT NULL CHECK (address LIKE '__:__:__:__:__:__'),
            recorded_at            TEXT NOT NULL CHECK (recorded_at LIKE '____-__-__T__:__:__%Z'),
            client_ip              TEXT NOT NULL,
            scale_address          TEXT CHECK (scale_address LIKE '__:__:__:__:__:__'
                                            OR scale_address LIKE '__:__:__:__:__'),
            ssid                   TEXT,
            wifi_rssi              INTEGER,
            queued                 INTEGER NOT NULL CHECK (queued >= 0),
            uptime_ms              INTEGER NOT NULL CHECK (uptime_ms >= 0),
            interval_ms            INTEGER NOT NULL CHECK (interval_ms > 0),
            firmware_version       TEXT NOT NULL,
            reset_reason           TEXT NOT NULL,
            boots                  INTEGER NOT NULL CHECK (boots >= 0),
            brownouts              INTEGER NOT NULL CHECK (brownouts >= 0 AND brownouts <= boots),
            free_heap              INTEGER CHECK (free_heap >= 0),
            largest_free_block     INTEGER CHECK (largest_free_block >= 0
                                              AND largest_free_block <= free_heap),
            -- The floor the heap reached since the board came up, which a ping
            -- taken after the pressure passed would otherwise never show.
            min_free_heap          INTEGER CHECK (min_free_heap >= 0),
            -- How long the run before this one lasted. Null where the board
            -- could not say: a supply that failed hard enough to clear the RTC
            -- domain leaves a boot looking like its first.
            previous_uptime_ms     INTEGER CHECK (previous_uptime_ms >= 0),
            -- Link churn since the board came up. Every reconnection owes a
            -- ping, so a count that climbs is extra traffic as well as a worse
            -- link, and both cost current.
            wifi_disconnects       INTEGER NOT NULL DEFAULT 0 CHECK (wifi_disconnects >= 0),
            wifi_disconnect_reason INTEGER NOT NULL DEFAULT 0 CHECK (wifi_disconnect_reason >= 0),
            -- The die rather than the regulator, and uncalibrated: worth reading
            -- as a direction across an afternoon and not as a temperature.
            chip_temp_c            REAL,
            cpu_mhz                INTEGER CHECK (cpu_mhz > 0),
            xtal_mhz               INTEGER CHECK (xtal_mhz > 0)
        )
        """,
        "CREATE INDEX device_pings_by_recency ON device_pings (address, recorded_at DESC, id DESC)",
        # The same fields on the devices row, so the snapshot and the history
        # answer the same questions and a reader needs only one of them to start.
        "ALTER TABLE devices ADD COLUMN min_free_heap INTEGER CHECK (min_free_heap >= 0)",
        "ALTER TABLE devices ADD COLUMN previous_uptime_ms INTEGER CHECK (previous_uptime_ms >= 0)",
        "ALTER TABLE devices ADD COLUMN wifi_disconnects INTEGER NOT NULL DEFAULT 0 "
        "CHECK (wifi_disconnects >= 0)",
        "ALTER TABLE devices ADD COLUMN wifi_disconnect_reason INTEGER NOT NULL DEFAULT 0 "
        "CHECK (wifi_disconnect_reason >= 0)",
        "ALTER TABLE devices ADD COLUMN chip_temp_c REAL",
        "ALTER TABLE devices ADD COLUMN cpu_mhz INTEGER CHECK (cpu_mhz > 0)",
        "ALTER TABLE devices ADD COLUMN xtal_mhz INTEGER CHECK (xtal_mhz > 0)",
    ),
    (
        # The ESP32 carries a die sensor, but not one Espressif supports on that
        # part: the driver has no backend for it and the ROM call behind it
        # answers with a constant once the radios are up. A column that can only
        # hold that constant reads as a measurement, which is worse than holding
        # nothing at all.
        "ALTER TABLE devices DROP COLUMN chip_temp_c",
        "ALTER TABLE device_pings DROP COLUMN chip_temp_c",
    ),
    (
        # A gateway listens for several scales, so the column that said which one
        # it was told to listen for has to say which ones. Rows already here carry
        # their single address into a list of one, which is what that board was
        # doing and what its firmware now reports for itself.
        #
        # A JSON array rather than columns or a table of its own, because there is
        # no fixed number of them and nothing here ever filters, joins or orders
        # on the field: it is a snapshot of what a board was told, read beside the
        # rest of its ping and never on its own. A child table would buy a query
        # nobody makes, at the price of one per device and one per ping kept.
        "ALTER TABLE devices ADD COLUMN scale_addresses TEXT "
        "CHECK (scale_addresses IS NULL OR json_type(scale_addresses) = 'array')",
        "UPDATE devices SET scale_addresses = json_array(scale_address) WHERE scale_address IS NOT NULL",
        "ALTER TABLE devices DROP COLUMN scale_address",
        "ALTER TABLE device_pings ADD COLUMN scale_addresses TEXT "
        "CHECK (scale_addresses IS NULL OR json_type(scale_addresses) = 'array')",
        "UPDATE device_pings SET scale_addresses = json_array(scale_address) WHERE scale_address IS NOT NULL",
        "ALTER TABLE device_pings DROP COLUMN scale_address",
    ),
]


def connect(db_path: Path) -> sqlite3.Connection:
    """Opens a connection in autocommit mode, so every write names its own transaction."""
    connection = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def migrate(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(connect(db_path)) as connection:
        applied = connection.execute("PRAGMA user_version").fetchone()[0]
        if applied > len(MIGRATIONS):
            raise RuntimeError(
                f"database at {db_path} is at schema version {applied}, "
                f"newer than the {len(MIGRATIONS)} migrations this build carries"
            )

        for version, statements in enumerate(MIGRATIONS[applied:], start=applied + 1):
            with transaction(connection):
                for statement in statements:
                    connection.execute(statement)
                # PRAGMA takes no placeholders, and version is a loop counter.
                connection.execute(f"PRAGMA user_version = {version}")
            log.info("schema migration applied", extra={"schema_version": version})
