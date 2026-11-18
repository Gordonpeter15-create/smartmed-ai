"""Save the synthetic dataset into the local SQLite database.

Usage (from the smartmed_ai folder, with the virtual environment active):

    python seed_data.py            # add sample data only if the database is empty
    python seed_data.py --reset    # DELETE data/smartmed.db and rebuild the sample data

The app also calls seed_database() on startup, so a brand-new database is
filled automatically. Existing data is never overwritten unless you ask for
--reset.
"""
import argparse
from datetime import date, datetime, timedelta

from database import DB_PATH, connect, initialize_database, record_opening_balances
from synthetic_data import DATASET_VERSION, DEFAULT_SEED, HISTORY_DAYS, generate_dataset


def seed_database(seed: int = DEFAULT_SEED, end_date: date | None = None) -> bool:
    """Fill an empty database with synthetic data. Returns True if data was added."""
    initialize_database()
    end_date = end_date or date.today() - timedelta(days=1)  # history ends yesterday
    with connect() as connection:
        if connection.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]:
            return False  # data already exists: leave it alone

        histories = generate_dataset(end_date, seed)
        for history in histories:
            profile = history.profile
            medicine_id = connection.execute(
                "INSERT INTO medicines (name, category, unit, reorder_level, lead_time_days) VALUES (?, ?, ?, ?, ?)",
                (profile.name, profile.category, profile.unit, profile.reorder_level, profile.lead_days),
            ).lastrowid
            connection.executemany(
                "INSERT INTO batches (medicine_id, batch_code, quantity, expiry_date) VALUES (?, ?, ?, ?)",
                [(medicine_id, b.code, b.quantity, b.expiry.isoformat()) for b in history.batches],
            )
            connection.executemany(
                """INSERT INTO incoming_orders (medicine_id, quantity, order_date, expected_arrival, note)
                   VALUES (?, ?, ?, ?, 'Synthetic order placed during the simulation')""",
                [(medicine_id, quantity, ordered.isoformat(), expected.isoformat())
                 for ordered, expected, quantity in history.open_orders],
            )
            connection.executemany(
                """INSERT INTO daily_consumption
                   (medicine_id, usage_date, quantity_dispensed, unmet_quantity, stockout)
                   VALUES (?, ?, ?, ?, ?)""",
                [(medicine_id, d.day.isoformat(), d.dispensed, d.unmet, int(d.stockout)) for d in history.days],
            )

        # Explain the starting stock of each batch with an opening-balance transaction.
        record_opening_balances(connection, end_date.isoformat())

        start_date = end_date - timedelta(days=HISTORY_DAYS - 1)
        connection.executemany(
            "INSERT INTO dataset_info (key, value) VALUES (?, ?)",
            [
                ("source", "synthetic"),
                ("generator_version", DATASET_VERSION),
                ("seed", str(seed)),
                ("start_date", start_date.isoformat()),
                ("end_date", end_date.isoformat()),
                ("created_at", datetime.now().isoformat(timespec="seconds")),
            ],
        )
    return True


def reset_database(seed: int = DEFAULT_SEED, end_date: date | None = None) -> None:
    """Delete the local database file and build a fresh synthetic dataset."""
    DB_PATH.unlink(missing_ok=True)
    seed_database(seed, end_date)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create SmartMed AI synthetic sample data.")
    parser.add_argument("--reset", action="store_true", help="delete the local database first")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: %(default)s)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="last day of history, YYYY-MM-DD (default: yesterday)")
    args = parser.parse_args()

    if args.reset:
        reset_database(args.seed, args.end_date)
        print(f"Database reset. Synthetic sample data rebuilt in {DB_PATH}.")
    elif seed_database(args.seed, args.end_date):
        print(f"Synthetic sample data created in {DB_PATH}.")
    else:
        print("The database already has data, so nothing was changed. Use --reset to rebuild it.")


if __name__ == "__main__":
    main()
