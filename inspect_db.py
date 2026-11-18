"""Look inside the local SmartMed AI database without changing anything.

Usage (from the smartmed_ai folder, with the virtual environment active):

    python inspect_db.py                         # summary of every table and a consistency check
    python inspect_db.py medicines               # first 10 rows of one table
    python inspect_db.py stock_transactions 25   # first 25 rows of one table

This script only READS the database.
"""
import sys

import pandas as pd

from database import DB_PATH, connect, dataset_info, reconciliation_mismatches

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)


def table_names() -> list[str]:
    with connect() as connection:
        return [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]


def summary() -> None:
    print(f"Database: {DB_PATH}\n")
    info = dataset_info()
    if info:
        print("Where the data came from (dataset_info):")
        for key, value in info.items():
            print(f"  {key:18} {value}")
        print()
    print("Rows per table:")
    with connect() as connection:
        for name in table_names():
            count = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"  {name:20} {count:>7,}")
        open_orders = connection.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'open'").fetchone()[0]
        units = connection.execute("SELECT COALESCE(SUM(quantity), 0) FROM batches").fetchone()[0]
    print(f"\nUnits in all batches: {units:,} · open incoming orders: {open_orders}")

    mismatches = reconciliation_mismatches()
    if mismatches:
        print(f"\n⚠️  {len(mismatches)} batch(es) do not match their transactions:")
        for row in mismatches:
            print(f"  {row['batch_code']}: quantity {row['quantity']} vs transactions {row['transaction_total']}")
    else:
        print("\n✅ Consistency check passed: every batch quantity equals the sum of its transactions.")
    print("\nTo see rows from a table:  python inspect_db.py <table name> [number of rows]")


def show_table(name: str, limit: int) -> None:
    if name not in table_names():
        print(f"No table called {name!r}. Tables: {', '.join(table_names())}")
        return
    with connect() as connection:
        frame = pd.read_sql_query(f"SELECT * FROM {name} LIMIT ?", connection, params=(limit,))
    print(frame.to_string(index=False) if not frame.empty else f"{name} is empty.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        summary()
    else:
        show_table(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10)
