import argparse
import sys

from db import db_cursor, init_schema_and_seed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize the PostgreSQL database, tables, and sample data."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop existing tables and recreate schema with fresh sample data.",
    )
    args = parser.parse_args()

    init_schema_and_seed(reset=args.reset)

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM admins) AS admins,
                (SELECT COUNT(*) FROM rooms) AS rooms,
                (SELECT COUNT(*) FROM customers) AS customers,
                (SELECT COUNT(*) FROM bookings) AS bookings,
                (SELECT COUNT(*) FROM payments) AS payments
            """
        )
        counts = cur.fetchone()

    print("Database setup complete.")
    print(
        f"Records: admins={counts['admins']}, rooms={counts['rooms']}, "
        f"customers={counts['customers']}, bookings={counts['bookings']}, "
        f"payments={counts['payments']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
