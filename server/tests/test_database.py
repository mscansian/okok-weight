import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from scale_server import database, readings

_INSERT = (
    "INSERT INTO readings (id, device_event_id, scale_address, weight_kg, measured_at, recorded_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def test_given_a_fresh_file_when_migrated_then_the_schema_is_at_the_latest_version(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "weights.db"

    database.migrate(db_path)

    with database.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(database.MIGRATIONS)


def test_given_a_migrated_file_when_migrated_again_then_nothing_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "weights.db"
    database.migrate(db_path)
    connection = database.connect(db_path)
    readings.record(connection, uuid4(), "08:b8:d0:12:34:56", 72.0)
    connection.close()

    database.migrate(db_path)

    connection = database.connect(db_path)
    assert readings.count(connection) == 1
    connection.close()


def test_given_a_file_from_a_newer_build_when_migrated_then_it_refuses_to_touch_it(tmp_path: Path) -> None:
    db_path = tmp_path / "weights.db"
    database.migrate(db_path)
    connection = database.connect(db_path)
    connection.execute(f"PRAGMA user_version = {len(database.MIGRATIONS) + 1}")
    connection.close()

    with pytest.raises(RuntimeError, match="newer than"):
        database.migrate(db_path)


def test_given_a_weight_outside_the_plausible_range_when_inserted_then_the_schema_refuses_it(tmp_path: Path) -> None:
    db_path = tmp_path / "weights.db"
    database.migrate(db_path)
    connection = database.connect(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT,
            (
                "d3b0",
                str(uuid4()),
                "08:b8:d0:12:34:56",
                900.0,
                "2026-08-29T18:00:00.000Z",
                "2026-08-29T18:00:00.000Z",
            ),
        )

    connection.close()


def test_given_a_timestamp_that_is_not_utc_when_inserted_then_the_schema_refuses_it(tmp_path: Path) -> None:
    db_path = tmp_path / "weights.db"
    database.migrate(db_path)
    connection = database.connect(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT,
            (
                "d3b0",
                str(uuid4()),
                "08:b8:d0:12:34:56",
                72.0,
                "2026-08-29T18:00:00+02:00",
                "2026-08-29T18:00:00+02:00",
            ),
        )

    connection.close()


def test_given_an_event_id_already_stored_when_reused_then_the_schema_refuses_it(tmp_path: Path) -> None:
    db_path = tmp_path / "weights.db"
    database.migrate(db_path)
    connection = database.connect(db_path)
    event_id = uuid4()
    readings.record(connection, event_id, "08:b8:d0:12:34:56", 72.0)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT,
            (
                "d3b0",
                str(event_id),
                "08:b8:d0:12:34:56",
                71.0,
                "2026-08-29T18:00:00.000Z",
                "2026-08-29T18:00:00.000Z",
            ),
        )

    connection.close()


def test_given_an_address_that_is_not_canonical_when_inserted_then_the_schema_refuses_it(tmp_path: Path) -> None:
    db_path = tmp_path / "weights.db"
    database.migrate(db_path)
    connection = database.connect(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT,
            (
                "d3b0",
                str(uuid4()),
                "08-b8-d0-12-34-56",
                72.0,
                "2026-08-29T18:00:00.000Z",
                "2026-08-29T18:00:00.000Z",
            ),
        )

    connection.close()
