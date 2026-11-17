"""Synthetic job: load a batch of 10 orders into data/orders.db (SQLite).

--fault:
  ack_timeout_retry  the first attempt commits, then a simulated timeout fires before the job sees the ack;
                     the retry loop inserts the batch again
  partial_batch      rows that fail validation are skipped silently
  crash              a genuine failure: the database path is wrong and the job exits 1
--idempotent uses a UNIQUE key and INSERT ... ON CONFLICT DO NOTHING, so retries are safe.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

BATCH = [(f"A-{1001 + i}", round(10 + i * 7.5, 2)) for i in range(10)]


def load(conn: sqlite3.Connection, rows, idempotent: bool, fault: str, attempt: int) -> None:
    sql = ("INSERT INTO orders(order_id, amount) VALUES (?, ?) ON CONFLICT(order_id) DO NOTHING" if idempotent
           else "INSERT INTO orders(order_id, amount) VALUES (?, ?)")
    for order_id, amount in rows:
        if fault == "partial_batch" and amount > 60:
            continue  # bug: "invalid" rows are dropped with no error
        conn.execute(sql, (order_id, amount))
    conn.commit()
    if fault == "ack_timeout_retry" and attempt == 1:
        raise TimeoutError("timed out waiting for commit acknowledgement")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", default="none", choices=["none", "ack_timeout_retry", "partial_batch", "crash"])
    ap.add_argument("--idempotent", action="store_true")
    args = ap.parse_args()
    db = Path("data/orders.db") if args.fault != "crash" else Path("missing-dir/orders.db")
    if args.fault != "crash":
        db.parent.mkdir(exist_ok=True)
    try:
        conn = sqlite3.connect(db)
    except sqlite3.OperationalError as exc:
        print(f"load_orders: FATAL cannot open {db}: {exc}", file=sys.stderr)
        return 1
    unique = "UNIQUE" if args.idempotent else ""
    conn.execute(f"CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, order_id TEXT {unique} NOT NULL, "
                 "amount REAL NOT NULL, loaded_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    for attempt in (1, 2, 3):
        try:
            load(conn, BATCH, args.idempotent, args.fault, attempt)
            break
        except TimeoutError as exc:
            print(f"load_orders: attempt {attempt} failed: {exc}; retrying")
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    print(f"load_orders: batch of {len(BATCH)} loaded; table now has {count} rows")
    print("load_orders: SUCCESS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
