"""SQLite setup and read helpers for the SmartMed AI prototype.

Functions here only create tables and read data. Rules for CHANGING stock
live in inventory.py, so every change goes through the same checks.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sqlite3

DB_PATH = Path(__file__).parent / "data" / "smartmed.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the database for a `with connect() as connection:` block.

    Changes are saved (committed) if the block finishes normally, undone
    (rolled back) if it raises an error, and the connection is always closed.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
        if connection.total_changes:
            global _saves
            _saves += 1
    finally:
        connection.close()


_saves = 0  # how many times this program has saved a change; used by data_version()


def data_version() -> tuple:
    """A small value that changes whenever the database changes.

    The app uses it to reuse earlier results until something new is saved.
    It counts this program's saves and also looks at the file itself, so a
    reset from the command line is noticed too.
    """
    try:
        stat = DB_PATH.stat()
    except FileNotFoundError:
        return (_saves, None)
    return (_saves, stat.st_ino, stat.st_mtime_ns, stat.st_size)


def initialize_database() -> None:
    """Create missing tables and upgrade older databases without losing data."""
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS medicines (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                unit TEXT NOT NULL,
                reorder_level INTEGER NOT NULL CHECK (reorder_level >= 0),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                -- Usual days from ordering to receiving a delivery (empty = not known yet).
                lead_time_days INTEGER CHECK (lead_time_days IS NULL OR lead_time_days BETWEEN 1 AND 365)
            );
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                batch_code TEXT NOT NULL UNIQUE,
                quantity INTEGER NOT NULL CHECK (quantity >= 0),
                expiry_date TEXT NOT NULL
            );
            -- Every change to a batch quantity is written here, so the current
            -- stock can always be explained: batch quantity = SUM(quantity_change).
            CREATE TABLE IF NOT EXISTS stock_transactions (
                id INTEGER PRIMARY KEY,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                batch_id INTEGER NOT NULL REFERENCES batches(id),
                transaction_date TEXT NOT NULL,
                transaction_type TEXT NOT NULL CHECK (
                    transaction_type IN ('opening_balance', 'receipt', 'dispense', 'adjustment')
                ),
                quantity_change INTEGER NOT NULL CHECK (quantity_change <> 0),
                reason TEXT,
                note TEXT,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                CHECK (transaction_type <> 'adjustment' OR COALESCE(reason, '') <> '')
            );
            -- Requests that could not be served because stock was not available.
            CREATE TABLE IF NOT EXISTS unmet_requests (
                id INTEGER PRIMARY KEY,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                request_date TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                note TEXT,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            -- Orders placed with suppliers that have not arrived yet ("open"),
            -- or that were later received or cancelled.
            CREATE TABLE IF NOT EXISTS incoming_orders (
                id INTEGER PRIMARY KEY,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                order_date TEXT NOT NULL,
                expected_arrival TEXT NOT NULL CHECK (expected_arrival >= order_date),
                status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'received', 'cancelled')),
                closed_date TEXT,
                note TEXT,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            -- SIMULATED daily history from synthetic_data.py. One row per medicine
            -- per day. "unmet_quantity" is demand that could not be served because
            -- stock ran out; without it, a stockout would look like a drop in demand.
            CREATE TABLE IF NOT EXISTS daily_consumption (
                id INTEGER PRIMARY KEY,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                usage_date TEXT NOT NULL,
                quantity_dispensed INTEGER NOT NULL CHECK (quantity_dispensed >= 0),
                unmet_quantity INTEGER NOT NULL DEFAULT 0 CHECK (unmet_quantity >= 0),
                stockout INTEGER NOT NULL DEFAULT 0 CHECK (stockout IN (0, 1)),
                UNIQUE (medicine_id, usage_date)
            );
            -- Simple key/value notes about where the data came from
            -- (for example: synthetic, generator version, seed, date range).
            CREATE TABLE IF NOT EXISTS dataset_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Upgrade: databases created before Milestone 3 have no "active" column.
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(medicines)")]
        if "active" not in columns:
            connection.execute("ALTER TABLE medicines ADD COLUMN active INTEGER NOT NULL DEFAULT 1")

        # Upgrade: databases created before Milestone 6 have no lead time. Fill it in
        # for the synthetic sample medicines from the generator's settings.
        if "lead_time_days" not in columns:
            connection.execute("ALTER TABLE medicines ADD COLUMN lead_time_days INTEGER")
            from synthetic_data import MEDICINES  # only needed for this one-off upgrade

            connection.executemany(
                "UPDATE medicines SET lead_time_days = ? WHERE name = ? AND lead_time_days IS NULL",
                [(profile.lead_days, profile.name) for profile in MEDICINES],
            )

        # Upgrade: batches created before Milestone 3 have no transactions yet.
        end_date = connection.execute("SELECT value FROM dataset_info WHERE key = 'end_date'").fetchone()
        record_opening_balances(connection, end_date[0] if end_date else date.today().isoformat())


def record_opening_balances(connection: sqlite3.Connection, balance_date: str) -> int:
    """Give every batch that has no transactions an 'opening_balance' transaction.

    This explains stock that existed before transactions were recorded (for
    example the synthetic starting stock). Returns how many were added.
    """
    cursor = connection.execute(
        """
        INSERT INTO stock_transactions
            (medicine_id, batch_id, transaction_date, transaction_type, quantity_change, note)
        SELECT b.medicine_id, b.id, ?, 'opening_balance', b.quantity,
               'Starting stock (synthetic sample data)'
        FROM batches AS b
        WHERE b.quantity > 0
          AND NOT EXISTS (SELECT 1 FROM stock_transactions AS t WHERE t.batch_id = b.id)
        """,
        (balance_date,),
    )
    return cursor.rowcount


def inventory_rows(include_inactive: bool = False) -> list[sqlite3.Row]:
    """Return each medicine with its total stock and nearest batch expiry."""
    with connect() as connection:
        return connection.execute(
            f"""
            SELECT m.id, m.name, m.category, m.unit, m.reorder_level, m.active, m.lead_time_days,
                   COALESCE(SUM(b.quantity), 0) AS stock,
                   MIN(CASE WHEN b.quantity > 0 THEN b.expiry_date END) AS nearest_expiry
            FROM medicines AS m
            LEFT JOIN batches AS b ON b.medicine_id = m.id
            {"" if include_inactive else "WHERE m.active = 1"}
            GROUP BY m.id
            ORDER BY m.name
            """
        ).fetchall()


def medicine_record_counts(medicine_id: int) -> dict[str, int]:
    """Count the records linked to a medicine (used before deleting it)."""
    with connect() as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table} WHERE medicine_id = ?", (medicine_id,)).fetchone()[0]
            for table in ("batches", "stock_transactions", "daily_consumption", "unmet_requests", "incoming_orders")
        }


def batch_rows(medicine_id: int, include_empty: bool = False) -> list[sqlite3.Row]:
    """Return a medicine's batches, earliest expiry first (FEFO order)."""
    with connect() as connection:
        return connection.execute(
            f"""
            SELECT id, batch_code, quantity, expiry_date
            FROM batches
            WHERE medicine_id = ? {"" if include_empty else "AND quantity > 0"}
            ORDER BY expiry_date, id
            """,
            (medicine_id,),
        ).fetchall()


def transaction_rows(medicine_id: int | None = None, limit: int = 500) -> list[sqlite3.Row]:
    """Return stock transactions, newest first."""
    with connect() as connection:
        return connection.execute(
            """
            SELECT t.id, t.transaction_date, m.name AS medicine, b.batch_code, t.transaction_type,
                   t.quantity_change, m.unit, t.reason, t.note, t.recorded_at
            FROM stock_transactions AS t
            JOIN medicines AS m ON m.id = t.medicine_id
            JOIN batches AS b ON b.id = t.batch_id
            WHERE (? IS NULL OR t.medicine_id = ?)
            ORDER BY t.transaction_date DESC, t.id DESC
            LIMIT ?
            """,
            (medicine_id, medicine_id, limit),
        ).fetchall()


def unmet_request_rows(medicine_id: int | None = None) -> list[sqlite3.Row]:
    """Return recorded unmet requests, newest first."""
    with connect() as connection:
        return connection.execute(
            """
            SELECT u.request_date, m.name AS medicine, u.quantity, m.unit, u.note, u.recorded_at
            FROM unmet_requests AS u JOIN medicines AS m ON m.id = u.medicine_id
            WHERE (? IS NULL OR u.medicine_id = ?)
            ORDER BY u.request_date DESC, u.id DESC
            """,
            (medicine_id, medicine_id),
        ).fetchall()


def reconciliation_mismatches() -> list[sqlite3.Row]:
    """Return batches whose quantity does NOT equal the sum of their transactions.

    An empty list means every batch quantity can be explained by its history.
    """
    with connect() as connection:
        return connection.execute(
            """
            SELECT b.batch_code, b.quantity, COALESCE(SUM(t.quantity_change), 0) AS transaction_total
            FROM batches AS b
            LEFT JOIN stock_transactions AS t ON t.batch_id = b.id
            GROUP BY b.id
            HAVING b.quantity <> COALESCE(SUM(t.quantity_change), 0)
            """
        ).fetchall()


def dataset_info() -> dict[str, str]:
    """Return the notes stored about the current dataset (empty if none)."""
    with connect() as connection:
        return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM dataset_info")}


def daily_consumption_rows() -> list[sqlite3.Row]:
    """Return every simulated daily consumption record with its medicine name and unit."""
    with connect() as connection:
        return connection.execute(
            """
            SELECT m.name AS medicine, m.unit, c.usage_date,
                   c.quantity_dispensed, c.unmet_quantity, c.stockout
            FROM daily_consumption AS c
            JOIN medicines AS m ON m.id = c.medicine_id
            ORDER BY m.name, c.usage_date
            """
        ).fetchall()


def expiring_batch_rows(within_days: int, today: date | None = None) -> list[sqlite3.Row]:
    """Return in-stock batches of active medicines that expire within `within_days` days.

    Already-expired batches that still hold stock are included (days_left <= 0).
    """
    today = today or date.today()
    limit = date.fromordinal(today.toordinal() + within_days)
    with connect() as connection:
        return connection.execute(
            """
            SELECT m.name AS medicine, b.batch_code, b.quantity, m.unit, b.expiry_date,
                   CAST(julianday(b.expiry_date) - julianday(?) AS INTEGER) AS days_left
            FROM batches AS b
            JOIN medicines AS m ON m.id = b.medicine_id
            WHERE m.active = 1 AND b.quantity > 0 AND b.expiry_date <= ?
            ORDER BY b.expiry_date, m.name
            """,
            (today.isoformat(), limit.isoformat()),
        ).fetchall()


def recorded_dispensing_rows(medicine_id: int | None = None) -> list[sqlite3.Row]:
    """Return daily totals dispensed through the app (from stock_transactions), oldest first."""
    with connect() as connection:
        return connection.execute(
            """
            SELECT t.transaction_date AS day, m.name AS medicine, m.unit,
                   SUM(-t.quantity_change) AS dispensed
            FROM stock_transactions AS t
            JOIN medicines AS m ON m.id = t.medicine_id
            WHERE t.transaction_type = 'dispense' AND (? IS NULL OR t.medicine_id = ?)
            GROUP BY t.medicine_id, t.transaction_date
            ORDER BY t.transaction_date
            """,
            (medicine_id, medicine_id),
        ).fetchall()


def order_rows(medicine_id: int | None = None, status: str | None = "open") -> list[sqlite3.Row]:
    """Return incoming orders (open ones by default), soonest expected arrival first."""
    with connect() as connection:
        return connection.execute(
            """
            SELECT o.id, o.medicine_id, m.name AS medicine, m.unit, o.quantity, o.order_date,
                   o.expected_arrival, o.status, o.closed_date, o.note
            FROM incoming_orders AS o
            JOIN medicines AS m ON m.id = o.medicine_id
            WHERE (? IS NULL OR o.medicine_id = ?) AND (? IS NULL OR o.status = ?)
            ORDER BY o.expected_arrival, o.id
            """,
            (medicine_id, medicine_id, status, status),
        ).fetchall()
