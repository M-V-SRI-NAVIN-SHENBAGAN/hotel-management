from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

import psycopg2
from psycopg2 import errors as pg_errors
from werkzeug.security import check_password_hash

from db import db_cursor, transaction

ROOM_TYPES = ("Single", "Double", "Deluxe", "Suite")
ROOM_STATUSES = ("Available", "Occupied", "Maintenance")
BOOKING_STATUSES = ("Confirmed", "Checked-in", "Checked-out", "Cancelled")
PAYMENT_METHODS = ("Cash", "Card", "UPI")
PAYMENT_STATUSES = ("Paid", "Partially Paid", "Pending")


class ValidationError(Exception):
    """Raised when user input or a business rule fails validation."""


def _parse_date(value: str, label: str) -> date:
    if not value or not value.strip():
        raise ValidationError(f"{label} is required.")
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError(f"{label} must be a valid date (YYYY-MM-DD).") from exc


def _parse_decimal(value: str, label: str, min_value: Decimal | None = None) -> Decimal:
    if value is None or str(value).strip() == "":
        raise ValidationError(f"{label} is required.")
    try:
        num = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{label} must be a valid number.") from exc
    if min_value is not None and num <= min_value:
        raise ValidationError(f"{label} must be greater than {min_value}.")
    return num


def _validate_email(email: str) -> str:
    cleaned = (email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
        raise ValidationError("Please enter a valid email address.")
    return cleaned


def _handle_integrity_error(exc: psycopg2.IntegrityError, context: str = "record") -> None:
    msg = str(exc)
    if isinstance(exc, pg_errors.UniqueViolation) or "unique" in msg.lower():
        if "room_number" in msg:
            raise ValidationError("That room number already exists.") from exc
        if "phone_number" in msg:
            raise ValidationError("A customer with that phone number already exists.") from exc
        if "email" in msg:
            raise ValidationError("A customer with that email address already exists.") from exc
        raise ValidationError(f"A duplicate {context} already exists.") from exc
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        raise ValidationError(
            f"Cannot complete operation because this {context} is referenced by existing records."
        ) from exc
    if isinstance(exc, pg_errors.CheckViolation):
        raise ValidationError(f"One or more {context} values violate validation rules.") from exc
    raise ValidationError(f"Database constraint error while saving {context}.") from exc


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate_admin(username: str, password: str):
    cleaned_user = (username or "").strip()
    if not cleaned_user or not password:
        return None
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT admin_id, username, password_hash, full_name
            FROM admins
            WHERE username = %s
            """,
            (cleaned_user,),
        )
        admin = cur.fetchone()
    if admin and check_password_hash(admin["password_hash"], password):
        return {
            "admin_id": admin["admin_id"],
            "username": admin["username"],
            "full_name": admin["full_name"],
        }
    return None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def get_dashboard_data():
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM rooms) AS total_rooms,
                (SELECT COUNT(*) FROM rooms WHERE availability_status = 'Available') AS available_rooms,
                (SELECT COUNT(*) FROM rooms WHERE availability_status = 'Occupied') AS occupied_rooms,
                (SELECT COUNT(*) FROM rooms WHERE availability_status = 'Maintenance') AS maintenance_rooms,
                (SELECT COUNT(*) FROM customers) AS total_customers,
                (SELECT COUNT(*) FROM bookings WHERE booking_status IN ('Confirmed', 'Checked-in')) AS active_bookings,
                (SELECT COUNT(*) FROM bookings WHERE check_in_date = CURRENT_DATE AND booking_status IN ('Confirmed', 'Checked-in')) AS today_checkins,
                (SELECT COUNT(*) FROM bookings WHERE check_out_date = CURRENT_DATE AND booking_status IN ('Checked-in', 'Checked-out')) AS today_checkouts,
                (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE payment_status IN ('Paid', 'Partially Paid')) AS total_revenue
            """
        )
        stats = cur.fetchone()

        cur.execute(
            """
            SELECT
                b.booking_id,
                b.check_in_date,
                b.check_out_date,
                b.num_nights,
                b.total_amount,
                b.booking_status,
                c.customer_id,
                c.full_name AS customer_name,
                c.phone_number,
                r.room_id,
                r.room_number,
                r.room_type
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE b.check_in_date = CURRENT_DATE
              AND b.booking_status IN ('Confirmed', 'Checked-in')
            ORDER BY
                CASE WHEN b.booking_status = 'Confirmed' THEN 0 ELSE 1 END,
                r.room_number ASC
            """
        )
        today_checkins_list = cur.fetchall()

        cur.execute(
            """
            SELECT
                b.booking_id,
                b.check_in_date,
                b.check_out_date,
                b.num_nights,
                b.total_amount,
                b.booking_status,
                c.customer_id,
                c.full_name AS customer_name,
                c.phone_number,
                r.room_id,
                r.room_number,
                r.room_type,
                COALESCE(p.paid_amount, 0) AS paid_amount,
                GREATEST(b.total_amount - COALESCE(p.paid_amount, 0), 0) AS outstanding_balance
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_amount
                FROM payments
                GROUP BY booking_id
            ) p ON p.booking_id = b.booking_id
            WHERE b.check_out_date = CURRENT_DATE
              AND b.booking_status IN ('Checked-in', 'Checked-out')
            ORDER BY
                CASE WHEN b.booking_status = 'Checked-in' THEN 0 ELSE 1 END,
                r.room_number ASC
            """
        )
        today_checkouts_list = cur.fetchall()

    return {
        "stats": stats,
        "today_checkins": today_checkins_list,
        "today_checkouts": today_checkouts_list,
    }


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------

def list_rooms(search: str = "", room_type: str = "", availability: str = ""):
    clauses = []
    params = []

    if search.strip():
        clauses.append("r.room_number ILIKE %s")
        params.append(f"%{search.strip()}%")
    if room_type in ROOM_TYPES:
        clauses.append("r.room_type = %s")
        params.append(room_type)
    if availability in ROOM_STATUSES:
        clauses.append("r.availability_status = %s")
        params.append(availability)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    query = f"""
        SELECT
            r.room_id,
            r.room_number,
            r.room_type,
            r.price_per_night,
            r.availability_status,
            r.description,
            next_res.booking_id AS next_booking_id,
            next_res.check_in_date AS next_check_in,
            next_res.check_out_date AS next_check_out
        FROM rooms r
        LEFT JOIN LATERAL (
            SELECT b.booking_id, b.check_in_date, b.check_out_date
            FROM bookings b
            WHERE b.room_id = r.room_id
              AND b.booking_status = 'Confirmed'
              AND b.check_out_date >= CURRENT_DATE
            ORDER BY b.check_in_date ASC
            LIMIT 1
        ) next_res ON TRUE
        {where_sql}
        ORDER BY r.room_number ASC
    """
    with db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def get_room(room_id: int):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT room_id, room_number, room_type, price_per_night, availability_status, description
            FROM rooms
            WHERE room_id = %s
            """,
            (room_id,),
        )
        return cur.fetchone()


def create_room(room_number: str, room_type: str, price_per_night: str, availability_status: str, description: str):
    num = (room_number or "").strip()
    if not num:
        raise ValidationError("Room number is required.")
    if room_type not in ROOM_TYPES:
        raise ValidationError("Please select a valid room type.")
    price = _parse_decimal(price_per_night, "Price per night", Decimal("0.00"))
    if availability_status not in ROOM_STATUSES:
        raise ValidationError("Please select a valid availability status.")
    desc = (description or "").strip() or None

    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO rooms (room_number, room_type, price_per_night, availability_status, description)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING room_id
                """,
                (num, room_type, price, availability_status, desc),
            )
            return cur.fetchone()["room_id"]
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "room")


def update_room(room_id: int, room_number: str, room_type: str, price_per_night: str, availability_status: str, description: str):
    num = (room_number or "").strip()
    if not num:
        raise ValidationError("Room number is required.")
    if room_type not in ROOM_TYPES:
        raise ValidationError("Please select a valid room type.")
    price = _parse_decimal(price_per_night, "Price per night", Decimal("0.00"))
    if availability_status not in ROOM_STATUSES:
        raise ValidationError("Please select a valid availability status.")
    desc = (description or "").strip() or None

    try:
        with transaction() as cur:
            cur.execute("SELECT room_id FROM rooms WHERE room_id = %s FOR UPDATE", (room_id,))
            if cur.fetchone() is None:
                raise ValidationError("Room not found.")

            cur.execute(
                """
                SELECT booking_id FROM bookings
                WHERE room_id = %s AND booking_status = 'Checked-in'
                LIMIT 1
                """,
                (room_id,),
            )
            active_checkin = cur.fetchone()
            if active_checkin and availability_status != "Occupied":
                raise ValidationError(
                    f"Room has an active checked-in guest (Booking #{active_checkin['booking_id']}) "
                    "and must stay Occupied until check-out."
                )

            cur.execute(
                """
                UPDATE rooms
                SET room_number = %s,
                    room_type = %s,
                    price_per_night = %s,
                    availability_status = %s,
                    description = %s
                WHERE room_id = %s
                """,
                (num, room_type, price, availability_status, desc, room_id),
            )
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "room")


def delete_room(room_id: int):
    try:
        with transaction() as cur:
            cur.execute("SELECT room_id, room_number FROM rooms WHERE room_id = %s FOR UPDATE", (room_id,))
            room = cur.fetchone()
            if room is None:
                raise ValidationError("Room not found.")

            cur.execute(
                "SELECT COUNT(*) AS cnt FROM bookings WHERE room_id = %s",
                (room_id,),
            )
            if cur.fetchone()["cnt"] > 0:
                raise ValidationError(
                    f"Cannot delete Room {room['room_number']} because it has booking history. "
                    "Set its availability status to Maintenance instead."
                )

            cur.execute("DELETE FROM rooms WHERE room_id = %s", (room_id,))
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "room")


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def list_customers(search: str = ""):
    clauses = []
    params = []
    if search.strip():
        like = f"%{search.strip()}%"
        clauses.append("(c.full_name ILIKE %s OR c.phone_number ILIKE %s OR c.email ILIKE %s)")
        params.extend([like, like, like])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT
            c.customer_id,
            c.full_name,
            c.phone_number,
            c.email,
            c.address,
            COUNT(b.booking_id) AS booking_count
        FROM customers c
        LEFT JOIN bookings b ON b.customer_id = c.customer_id
        {where_sql}
        GROUP BY c.customer_id
        ORDER BY c.full_name ASC, c.customer_id ASC
    """
    with db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def get_customer(customer_id: int):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT customer_id, full_name, phone_number, email, address, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (customer_id,),
        )
        return cur.fetchone()


def _insert_customer_with_cursor(cur, full_name: str, phone_number: str, email: str, address: str) -> int:
    name = (full_name or "").strip()
    phone = (phone_number or "").strip()
    addr = (address or "").strip()
    if not name:
        raise ValidationError("Customer full name is required.")
    if not phone:
        raise ValidationError("Customer phone number is required.")
    valid_email = _validate_email(email)
    if not addr:
        raise ValidationError("Customer address is required.")

    cur.execute(
        """
        INSERT INTO customers (full_name, phone_number, email, address)
        VALUES (%s, %s, %s, %s)
        RETURNING customer_id
        """,
        (name, phone, valid_email, addr),
    )
    return cur.fetchone()["customer_id"]


def create_customer(full_name: str, phone_number: str, email: str, address: str) -> int:
    try:
        with transaction() as cur:
            return _insert_customer_with_cursor(cur, full_name, phone_number, email, address)
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "customer")


def update_customer(customer_id: int, full_name: str, phone_number: str, email: str, address: str):
    name = (full_name or "").strip()
    phone = (phone_number or "").strip()
    addr = (address or "").strip()
    if not name:
        raise ValidationError("Full name is required.")
    if not phone:
        raise ValidationError("Phone number is required.")
    valid_email = _validate_email(email)
    if not addr:
        raise ValidationError("Address is required.")

    try:
        with transaction() as cur:
            cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s FOR UPDATE", (customer_id,))
            if cur.fetchone() is None:
                raise ValidationError("Customer not found.")
            cur.execute(
                """
                UPDATE customers
                SET full_name = %s,
                    phone_number = %s,
                    email = %s,
                    address = %s
                WHERE customer_id = %s
                """,
                (name, phone, valid_email, addr, customer_id),
            )
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "customer")


def get_customer_bookings(customer_id: int):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                b.booking_id,
                b.check_in_date,
                b.check_out_date,
                b.num_nights,
                b.total_amount,
                b.booking_status,
                r.room_id,
                r.room_number,
                r.room_type,
                COALESCE(p.paid_amount, 0) AS paid_amount,
                CASE
                    WHEN b.booking_status = 'Cancelled' THEN 0
                    ELSE GREATEST(b.total_amount - COALESCE(p.paid_amount, 0), 0)
                END AS outstanding_balance
            FROM bookings b
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_amount
                FROM payments
                GROUP BY booking_id
            ) p ON p.booking_id = b.booking_id
            WHERE b.customer_id = %s
            ORDER BY b.check_in_date DESC, b.booking_id DESC
            """,
            (customer_id,),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

def list_bookings(search: str = "", status: str = ""):
    clauses = []
    params = []

    if search.strip():
        term = search.strip().lstrip("#")
        like = f"%{search.strip()}%"
        if term.isdigit():
            clauses.append("(b.booking_id = %s OR c.full_name ILIKE %s)")
            params.extend([int(term), like])
        else:
            clauses.append("c.full_name ILIKE %s")
            params.append(like)

    if status in BOOKING_STATUSES:
        clauses.append("b.booking_status = %s")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    query = f"""
        SELECT
            b.booking_id,
            b.check_in_date,
            b.check_out_date,
            b.num_nights,
            b.total_amount,
            b.booking_status,
            b.created_at,
            c.customer_id,
            c.full_name AS customer_name,
            c.phone_number,
            r.room_id,
            r.room_number,
            r.room_type,
            r.price_per_night,
            COALESCE(p.paid_amount, 0) AS paid_amount,
            CASE
                WHEN b.booking_status = 'Cancelled' THEN 0
                ELSE GREATEST(b.total_amount - COALESCE(p.paid_amount, 0), 0)
            END AS outstanding_balance
        FROM bookings b
        JOIN customers c ON c.customer_id = b.customer_id
        JOIN rooms r ON r.room_id = b.room_id
        LEFT JOIN (
            SELECT
                booking_id,
                SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_amount
            FROM payments
            GROUP BY booking_id
        ) p ON p.booking_id = b.booking_id
        {where_sql}
        ORDER BY b.booking_id DESC
    """
    with db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def get_booking(booking_id: int):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                b.*,
                c.full_name AS customer_name,
                c.phone_number,
                c.email,
                c.address,
                r.room_number,
                r.room_type,
                r.price_per_night,
                r.availability_status AS room_status,
                COALESCE(p.paid_amount, 0) AS paid_amount,
                CASE
                    WHEN b.booking_status = 'Cancelled' THEN 0
                    ELSE GREATEST(b.total_amount - COALESCE(p.paid_amount, 0), 0)
                END AS outstanding_balance
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_amount
                FROM payments
                GROUP BY booking_id
            ) p ON p.booking_id = b.booking_id
            WHERE b.booking_id = %s
            """,
            (booking_id,),
        )
        booking = cur.fetchone()
        if not booking:
            return None

        cur.execute(
            """
            SELECT payment_id, amount, payment_date, payment_method, payment_status, notes
            FROM payments
            WHERE booking_id = %s
            ORDER BY payment_date DESC, payment_id DESC
            """,
            (booking_id,),
        )
        payments = cur.fetchall()

    return {"booking": booking, "payments": payments}


def get_available_rooms(check_in: date | None = None, check_out: date | None = None, exclude_booking_id: int | None = None):
    params = []
    overlap_clause = ""
    if check_in and check_out and check_out > check_in:
        exclude_sql = ""
        if exclude_booking_id is not None:
            exclude_sql = "AND b.booking_id != %s"
            params_inner = [check_out, check_in, exclude_booking_id]
        else:
            params_inner = [check_out, check_in]
        overlap_clause = f"""
            AND NOT EXISTS (
                SELECT 1
                FROM bookings b
                WHERE b.room_id = r.room_id
                  AND b.booking_status IN ('Confirmed', 'Checked-in')
                  AND b.check_in_date < %s
                  AND b.check_out_date > %s
                  {exclude_sql}
            )
        """
        params.extend(params_inner)

    query = f"""
        SELECT room_id, room_number, room_type, price_per_night, availability_status, description
        FROM rooms r
        WHERE r.availability_status = 'Available'
        {overlap_clause}
        ORDER BY r.room_number ASC
    """
    with db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _check_overlapping_booking(cur, room_id: int, check_in: date, check_out: date, exclude_booking_id: int | None = None):
    params = [room_id, check_out, check_in]
    exclude_sql = ""
    if exclude_booking_id is not None:
        exclude_sql = "AND booking_id != %s"
        params.append(exclude_booking_id)

    cur.execute(
        f"""
        SELECT booking_id, check_in_date, check_out_date, booking_status
        FROM bookings
        WHERE room_id = %s
          AND booking_status IN ('Confirmed', 'Checked-in')
          AND check_in_date < %s
          AND check_out_date > %s
          {exclude_sql}
        ORDER BY check_in_date ASC
        FOR UPDATE
        """,
        params,
    )
    return cur.fetchone()


def create_booking(
    customer_mode: str,
    customer_id_raw: str,
    new_customer_data: dict,
    room_id_raw: str,
    check_in_str: str,
    check_out_str: str,
    notes: str = "",
) -> int:
    check_in = _parse_date(check_in_str, "Check-in date")
    check_out = _parse_date(check_out_str, "Check-out date")
    if check_out <= check_in:
        raise ValidationError("Check-out date must be after check-in date.")

    if not room_id_raw or not str(room_id_raw).isdigit():
        raise ValidationError("Please select a valid room.")
    room_id = int(room_id_raw)

    num_nights = (check_out - check_in).days
    cleaned_notes = (notes or "").strip() or None

    try:
        with transaction() as cur:
            if customer_mode == "new":
                customer_id = _insert_customer_with_cursor(
                    cur,
                    new_customer_data.get("full_name", ""),
                    new_customer_data.get("phone_number", ""),
                    new_customer_data.get("email", ""),
                    new_customer_data.get("address", ""),
                )
            else:
                if not customer_id_raw or not str(customer_id_raw).isdigit():
                    raise ValidationError("Please select an existing customer.")
                customer_id = int(customer_id_raw)
                cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s", (customer_id,))
                if cur.fetchone() is None:
                    raise ValidationError("Selected customer does not exist.")

            # Lock room row to serialize concurrent booking checks on the same room
            cur.execute(
                """
                SELECT room_id, room_number, price_per_night, availability_status
                FROM rooms
                WHERE room_id = %s
                FOR UPDATE
                """,
                (room_id,),
            )
            room = cur.fetchone()
            if room is None:
                raise ValidationError("Selected room does not exist.")
            if room["availability_status"] != "Available":
                raise ValidationError(
                    f"Room {room['room_number']} is currently {room['availability_status']} and cannot be booked."
                )

            conflict = _check_overlapping_booking(cur, room_id, check_in, check_out)
            if conflict:
                raise ValidationError(
                    f"Room {room['room_number']} already has an active reservation "
                    f"(Booking #{conflict['booking_id']}: {conflict['check_in_date']} to {conflict['check_out_date']})."
                )

            total_amount = (Decimal(num_nights) * room["price_per_night"]).quantize(Decimal("0.01"))
            cur.execute(
                """
                INSERT INTO bookings (
                    customer_id, room_id, check_in_date, check_out_date,
                    num_nights, total_amount, booking_status, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'Confirmed', %s)
                RETURNING booking_id
                """,
                (customer_id, room_id, check_in, check_out, num_nights, total_amount, cleaned_notes),
            )
            return cur.fetchone()["booking_id"]
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "booking")


def update_booking(
    booking_id: int,
    room_id_raw: str,
    check_in_str: str,
    check_out_str: str,
    new_status: str,
    notes: str = "",
):
    check_in = _parse_date(check_in_str, "Check-in date")
    check_out = _parse_date(check_out_str, "Check-out date")
    if check_out <= check_in:
        raise ValidationError("Check-out date must be after check-in date.")
    if new_status not in BOOKING_STATUSES:
        raise ValidationError("Invalid booking status.")
    if not room_id_raw or not str(room_id_raw).isdigit():
        raise ValidationError("Please select a valid room.")
    target_room_id = int(room_id_raw)
    cleaned_notes = (notes or "").strip() or None

    try:
        with transaction() as cur:
            cur.execute("SELECT * FROM bookings WHERE booking_id = %s FOR UPDATE", (booking_id,))
            existing = cur.fetchone()
            if existing is None:
                raise ValidationError("Booking not found.")

            old_status = existing["booking_status"]
            old_room_id = existing["room_id"]

            if old_status in ("Checked-out", "Cancelled"):
                if (
                    target_room_id != old_room_id
                    or check_in != existing["check_in_date"]
                    or check_out != existing["check_out_date"]
                    or new_status != old_status
                ):
                    raise ValidationError(
                        f"Bookings with status '{old_status}' cannot modify room, dates, or status."
                    )
                cur.execute(
                    """
                    UPDATE bookings
                    SET notes = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE booking_id = %s
                    """,
                    (cleaned_notes, booking_id),
                )
                return

            # Lock involved rooms in deterministic order to avoid deadlocks
            room_ids = sorted({old_room_id, target_room_id})
            cur.execute(
                """
                SELECT room_id, room_number, price_per_night, availability_status
                FROM rooms
                WHERE room_id = ANY(%s)
                ORDER BY room_id ASC
                FOR UPDATE
                """,
                (room_ids,),
            )
            rooms_map = {row["room_id"]: row for row in cur.fetchall()}
            if target_room_id not in rooms_map:
                raise ValidationError("Selected room does not exist.")
            target_room = rooms_map[target_room_id]

            # Room change availability guard
            if target_room_id != old_room_id and target_room["availability_status"] != "Available":
                raise ValidationError(
                    f"Room {target_room['room_number']} is currently {target_room['availability_status']} "
                    "and cannot be assigned."
                )

            if new_status in ("Confirmed", "Checked-in"):
                if target_room["availability_status"] == "Maintenance":
                    raise ValidationError(
                        f"Room {target_room['room_number']} is under Maintenance and cannot be booked."
                    )
                conflict = _check_overlapping_booking(
                    cur, target_room_id, check_in, check_out, exclude_booking_id=booking_id
                )
                if conflict:
                    raise ValidationError(
                        f"Room {target_room['room_number']} overlaps with Booking #{conflict['booking_id']} "
                        f"({conflict['check_in_date']} to {conflict['check_out_date']})."
                    )

            if new_status == "Checked-in":
                cur.execute(
                    """
                    SELECT booking_id FROM bookings
                    WHERE room_id = %s AND booking_status = 'Checked-in' AND booking_id != %s
                    LIMIT 1
                    """,
                    (target_room_id, booking_id),
                )
                other_checked_in = cur.fetchone()
                if other_checked_in:
                    raise ValidationError(
                        f"Room {target_room['room_number']} already has checked-in guest "
                        f"(Booking #{other_checked_in['booking_id']})."
                    )
                if date.today() < check_in:
                    early_conflict = _check_overlapping_booking(
                        cur, target_room_id, date.today(), check_out, exclude_booking_id=booking_id
                    )
                    if early_conflict:
                        raise ValidationError(
                            f"Cannot check in early: Booking #{early_conflict['booking_id']} occupies "
                            f"Room {target_room['room_number']} starting {early_conflict['check_in_date']}."
                        )

            num_nights = (check_out - check_in).days
            total_amount = (Decimal(num_nights) * target_room["price_per_night"]).quantize(Decimal("0.01"))

            cur.execute(
                """
                UPDATE bookings
                SET room_id = %s,
                    check_in_date = %s,
                    check_out_date = %s,
                    num_nights = %s,
                    total_amount = %s,
                    booking_status = %s,
                    notes = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE booking_id = %s
                """,
                (target_room_id, check_in, check_out, num_nights, total_amount, new_status, cleaned_notes, booking_id),
            )

            # Synchronize room availability status consistently
            if old_status == "Checked-in" and (new_status != "Checked-in" or target_room_id != old_room_id):
                cur.execute(
                    """
                    SELECT 1 FROM bookings
                    WHERE room_id = %s AND booking_status = 'Checked-in' AND booking_id != %s
                    LIMIT 1
                    """,
                    (old_room_id, booking_id),
                )
                if cur.fetchone() is None and rooms_map[old_room_id]["availability_status"] == "Occupied":
                    cur.execute(
                        "UPDATE rooms SET availability_status = 'Available' WHERE room_id = %s",
                        (old_room_id,),
                    )

            if new_status == "Checked-in":
                cur.execute(
                    "UPDATE rooms SET availability_status = 'Occupied' WHERE room_id = %s",
                    (target_room_id,),
                )
    except psycopg2.IntegrityError as exc:
        _handle_integrity_error(exc, "booking")


def cancel_booking(booking_id: int):
    with transaction() as cur:
        cur.execute("SELECT booking_id, room_id, booking_status FROM bookings WHERE booking_id = %s FOR UPDATE", (booking_id,))
        booking = cur.fetchone()
        if booking is None:
            raise ValidationError("Booking not found.")
        if booking["booking_status"] in ("Checked-out", "Cancelled"):
            raise ValidationError(f"Booking #{booking_id} is already {booking['booking_status']}.")

        cur.execute("SELECT room_id, availability_status FROM rooms WHERE room_id = %s FOR UPDATE", (booking["room_id"],))
        room = cur.fetchone()

        cur.execute(
            """
            UPDATE bookings
            SET booking_status = 'Cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE booking_id = %s
            """,
            (booking_id,),
        )

        if booking["booking_status"] == "Checked-in" and room["availability_status"] == "Occupied":
            cur.execute(
                """
                SELECT 1 FROM bookings
                WHERE room_id = %s AND booking_status = 'Checked-in' AND booking_id != %s
                LIMIT 1
                """,
                (booking["room_id"], booking_id),
            )
            if cur.fetchone() is None:
                cur.execute(
                    "UPDATE rooms SET availability_status = 'Available' WHERE room_id = %s",
                    (booking["room_id"],),
                )


# ---------------------------------------------------------------------------
# Check-in / Check-out
# ---------------------------------------------------------------------------

def get_checkin_checkout_board(search: str = ""):
    search_clause = ""
    params = []
    if search.strip():
        term = search.strip().lstrip("#")
        like = f"%{search.strip()}%"
        if term.isdigit():
            search_clause = "AND (b.booking_id = %s OR c.full_name ILIKE %s OR r.room_number ILIKE %s)"
            params = [int(term), like, like]
        else:
            search_clause = "AND (c.full_name ILIKE %s OR r.room_number ILIKE %s)"
            params = [like, like]

    with db_cursor() as cur:
        cur.execute(
            f"""
            SELECT
                b.booking_id,
                b.check_in_date,
                b.check_out_date,
                b.num_nights,
                b.total_amount,
                b.booking_status,
                c.customer_id,
                c.full_name AS customer_name,
                c.phone_number,
                r.room_id,
                r.room_number,
                r.room_type,
                r.availability_status AS room_status
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE b.booking_status = 'Confirmed'
            {search_clause}
            ORDER BY
                CASE
                    WHEN b.check_in_date = CURRENT_DATE THEN 0
                    WHEN b.check_in_date < CURRENT_DATE THEN 1
                    ELSE 2
                END,
                b.check_in_date ASC,
                r.room_number ASC
            """,
            params,
        )
        pending_checkins = cur.fetchall()

        cur.execute(
            f"""
            SELECT
                b.booking_id,
                b.check_in_date,
                b.check_out_date,
                b.num_nights,
                b.total_amount,
                b.booking_status,
                c.customer_id,
                c.full_name AS customer_name,
                c.phone_number,
                r.room_id,
                r.room_number,
                r.room_type,
                COALESCE(p.paid_amount, 0) AS paid_amount,
                GREATEST(b.total_amount - COALESCE(p.paid_amount, 0), 0) AS outstanding_balance
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_amount
                FROM payments
                GROUP BY booking_id
            ) p ON p.booking_id = b.booking_id
            WHERE b.booking_status = 'Checked-in'
            {search_clause}
            ORDER BY
                CASE
                    WHEN b.check_out_date <= CURRENT_DATE THEN 0
                    ELSE 1
                END,
                b.check_out_date ASC,
                r.room_number ASC
            """,
            params,
        )
        active_checkouts = cur.fetchall()

        cur.execute(
            f"""
            SELECT
                b.booking_id,
                b.check_in_date,
                b.check_out_date,
                b.total_amount,
                b.updated_at,
                c.full_name AS customer_name,
                r.room_number,
                r.room_type,
                r.availability_status AS room_status
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE b.booking_status = 'Checked-out'
            {search_clause}
            ORDER BY b.updated_at DESC, b.booking_id DESC
            LIMIT 10
            """,
            params,
        )
        recent_checkouts = cur.fetchall()

    return {
        "pending_checkins": pending_checkins,
        "active_checkouts": active_checkouts,
        "recent_checkouts": recent_checkouts,
    }


def check_in_booking(booking_id: int):
    with transaction() as cur:
        cur.execute(
            """
            SELECT booking_id, room_id, check_in_date, check_out_date, booking_status
            FROM bookings
            WHERE booking_id = %s
            FOR UPDATE
            """,
            (booking_id,),
        )
        booking = cur.fetchone()
        if booking is None:
            raise ValidationError("Booking not found.")
        if booking["booking_status"] != "Confirmed":
            raise ValidationError(
                f"Only Confirmed bookings can be checked in (current status: {booking['booking_status']})."
            )

        cur.execute(
            "SELECT room_id, room_number, availability_status FROM rooms WHERE room_id = %s FOR UPDATE",
            (booking["room_id"],),
        )
        room = cur.fetchone()
        if room["availability_status"] == "Maintenance":
            raise ValidationError(
                f"Room {room['room_number']} is currently under Maintenance and cannot be checked in."
            )

        cur.execute(
            """
            SELECT booking_id FROM bookings
            WHERE room_id = %s AND booking_status = 'Checked-in' AND booking_id != %s
            LIMIT 1
            """,
            (booking["room_id"], booking_id),
        )
        occupied_by = cur.fetchone()
        if occupied_by:
            raise ValidationError(
                f"Room {room['room_number']} is currently occupied by Booking #{occupied_by['booking_id']}."
            )
        if room["availability_status"] == "Occupied":
            raise ValidationError(
                f"Room {room['room_number']} is marked Occupied and cannot be checked in."
            )

        today = date.today()
        if today < booking["check_in_date"]:
            conflict = _check_overlapping_booking(
                cur,
                booking["room_id"],
                today,
                booking["check_out_date"],
                exclude_booking_id=booking_id,
            )
            if conflict:
                raise ValidationError(
                    f"Cannot check in early: Booking #{conflict['booking_id']} reserves this room "
                    f"from {conflict['check_in_date']} to {conflict['check_out_date']}."
                )

        cur.execute(
            """
            UPDATE bookings
            SET booking_status = 'Checked-in', updated_at = CURRENT_TIMESTAMP
            WHERE booking_id = %s
            """,
            (booking_id,),
        )
        cur.execute(
            "UPDATE rooms SET availability_status = 'Occupied' WHERE room_id = %s",
            (booking["room_id"],),
        )
        return room["room_number"]


def check_out_booking(booking_id: int):
    with transaction() as cur:
        cur.execute(
            """
            SELECT booking_id, room_id, booking_status
            FROM bookings
            WHERE booking_id = %s
            FOR UPDATE
            """,
            (booking_id,),
        )
        booking = cur.fetchone()
        if booking is None:
            raise ValidationError("Booking not found.")
        if booking["booking_status"] != "Checked-in":
            raise ValidationError(
                f"Only Checked-in bookings can be checked out (current status: {booking['booking_status']})."
            )

        cur.execute(
            "SELECT room_id, room_number, availability_status FROM rooms WHERE room_id = %s FOR UPDATE",
            (booking["room_id"],),
        )
        room = cur.fetchone()

        cur.execute(
            """
            UPDATE bookings
            SET booking_status = 'Checked-out', updated_at = CURRENT_TIMESTAMP
            WHERE booking_id = %s
            """,
            (booking_id,),
        )

        # Make room available for future bookings while keeping future Confirmed reservations intact
        cur.execute(
            """
            SELECT 1 FROM bookings
            WHERE room_id = %s AND booking_status = 'Checked-in' AND booking_id != %s
            LIMIT 1
            """,
            (booking["room_id"], booking_id),
        )
        if cur.fetchone() is None and room["availability_status"] != "Maintenance":
            cur.execute(
                "UPDATE rooms SET availability_status = 'Available' WHERE room_id = %s",
                (booking["room_id"],),
            )
        return room["room_number"]


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def list_payments(search: str = "", method: str = "", status: str = ""):
    clauses = []
    params = []

    if search.strip():
        term = search.strip().lstrip("#")
        like = f"%{search.strip()}%"
        if term.isdigit():
            clauses.append("(p.payment_id = %s OR p.booking_id = %s OR c.full_name ILIKE %s)")
            params.extend([int(term), int(term), like])
        else:
            clauses.append("c.full_name ILIKE %s")
            params.append(like)

    if method in PAYMENT_METHODS:
        clauses.append("p.payment_method = %s")
        params.append(method)
    if status in PAYMENT_STATUSES:
        clauses.append("p.payment_status = %s")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db_cursor() as cur:
        cur.execute(
            f"""
            SELECT
                p.payment_id,
                p.booking_id,
                p.amount,
                p.payment_date,
                p.payment_method,
                p.payment_status,
                p.notes,
                b.total_amount AS booking_total,
                b.booking_status,
                c.customer_id,
                c.full_name AS customer_name,
                r.room_number,
                COALESCE(bp.paid_total, 0) AS booking_paid_total,
                CASE
                    WHEN b.booking_status = 'Cancelled' THEN 0
                    ELSE GREATEST(b.total_amount - COALESCE(bp.paid_total, 0), 0)
                END AS outstanding_balance
            FROM payments p
            JOIN bookings b ON b.booking_id = p.booking_id
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_total
                FROM payments
                GROUP BY booking_id
            ) bp ON bp.booking_id = p.booking_id
            {where_sql}
            ORDER BY p.payment_date DESC, p.payment_id DESC
            """,
            params,
        )
        payments = cur.fetchall()

        cur.execute(
            """
            SELECT
                COALESCE(SUM(amount) FILTER (WHERE payment_status IN ('Paid', 'Partially Paid')), 0) AS total_revenue,
                COALESCE(SUM(amount) FILTER (WHERE payment_status = 'Pending'), 0) AS total_pending,
                COUNT(*) AS total_transactions
            FROM payments
            """
        )
        summary = cur.fetchone()

    return {"payments": payments, "summary": summary}


def get_payment(payment_id: int):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                p.*,
                b.check_in_date,
                b.check_out_date,
                b.num_nights,
                b.total_amount AS booking_total,
                b.booking_status,
                c.customer_id,
                c.full_name AS customer_name,
                c.phone_number,
                c.email,
                r.room_number,
                r.room_type,
                COALESCE(bp.paid_total, 0) AS booking_paid_total,
                CASE
                    WHEN b.booking_status = 'Cancelled' THEN 0
                    ELSE GREATEST(b.total_amount - COALESCE(bp.paid_total, 0), 0)
                END AS outstanding_balance
            FROM payments p
            JOIN bookings b ON b.booking_id = p.booking_id
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_total
                FROM payments
                GROUP BY booking_id
            ) bp ON bp.booking_id = p.booking_id
            WHERE p.payment_id = %s
            """,
            (payment_id,),
        )
        return cur.fetchone()


def get_bookings_for_payment():
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                b.booking_id,
                b.total_amount,
                b.booking_status,
                b.check_in_date,
                b.check_out_date,
                c.full_name AS customer_name,
                r.room_number,
                COALESCE(bp.paid_total, 0) AS paid_total,
                GREATEST(b.total_amount - COALESCE(bp.paid_total, 0), 0) AS outstanding_balance
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_total
                FROM payments
                GROUP BY booking_id
            ) bp ON bp.booking_id = b.booking_id
            WHERE b.booking_status != 'Cancelled'
            ORDER BY
                CASE WHEN GREATEST(b.total_amount - COALESCE(bp.paid_total, 0), 0) > 0 THEN 0 ELSE 1 END,
                b.booking_id DESC
            """
        )
        return cur.fetchall()


def record_payment(
    booking_id_raw: str,
    amount_raw: str,
    payment_date_str: str,
    payment_method: str,
    payment_status: str,
    notes: str = "",
) -> tuple[int, str]:
    if not booking_id_raw or not str(booking_id_raw).isdigit():
        raise ValidationError("Please select a valid booking.")
    booking_id = int(booking_id_raw)
    amount = _parse_decimal(amount_raw, "Payment amount", Decimal("0.00"))
    payment_date = _parse_date(payment_date_str, "Payment date")
    if payment_method not in PAYMENT_METHODS:
        raise ValidationError("Please select a valid payment method.")
    if payment_status not in PAYMENT_STATUSES:
        raise ValidationError("Please select a valid payment status.")
    cleaned_notes = (notes or "").strip() or None

    with transaction() as cur:
        cur.execute(
            """
            SELECT booking_id, total_amount, booking_status
            FROM bookings
            WHERE booking_id = %s
            FOR UPDATE
            """,
            (booking_id,),
        )
        booking = cur.fetchone()
        if booking is None:
            raise ValidationError("Selected booking does not exist.")
        if booking["booking_status"] == "Cancelled":
            raise ValidationError("Cannot record payment for a cancelled booking.")

        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS paid_total
            FROM payments
            WHERE booking_id = %s AND payment_status IN ('Paid', 'Partially Paid')
            """,
            (booking_id,),
        )
        paid_total = cur.fetchone()["paid_total"]
        outstanding = (booking["total_amount"] - paid_total).quantize(Decimal("0.01"))

        if outstanding <= Decimal("0.00"):
            raise ValidationError(f"Booking #{booking_id} is already paid in full.")
        if amount > outstanding:
            raise ValidationError(
                f"Payment amount ({amount:.2f}) exceeds the remaining balance ({outstanding:.2f})."
            )

        effective_status = payment_status
        if payment_status in ("Paid", "Partially Paid"):
            if amount < outstanding:
                effective_status = "Partially Paid"
            else:
                effective_status = "Paid"

        cur.execute(
            """
            INSERT INTO payments (booking_id, amount, payment_date, payment_method, payment_status, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING payment_id
            """,
            (booking_id, amount, payment_date, payment_method, effective_status, cleaned_notes),
        )
        return cur.fetchone()["payment_id"], effective_status


def mark_payment_paid(payment_id: int) -> str:
    with transaction() as cur:
        cur.execute("SELECT * FROM payments WHERE payment_id = %s FOR UPDATE", (payment_id,))
        payment = cur.fetchone()
        if payment is None:
            raise ValidationError("Payment record not found.")
        if payment["payment_status"] != "Pending":
            raise ValidationError("Only Pending payments can be marked as Paid.")

        cur.execute(
            "SELECT booking_id, total_amount, booking_status FROM bookings WHERE booking_id = %s FOR UPDATE",
            (payment["booking_id"],),
        )
        booking = cur.fetchone()
        if booking["booking_status"] == "Cancelled":
            raise ValidationError("Cannot settle payment for a cancelled booking.")

        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS paid_total
            FROM payments
            WHERE booking_id = %s AND payment_status IN ('Paid', 'Partially Paid')
            """,
            (payment["booking_id"],),
        )
        paid_total = cur.fetchone()["paid_total"]
        outstanding = (booking["total_amount"] - paid_total).quantize(Decimal("0.01"))
        if outstanding <= Decimal("0.00"):
            raise ValidationError("This booking is already paid in full.")
        if payment["amount"] > outstanding:
            raise ValidationError(
                f"Pending payment ({payment['amount']:.2f}) exceeds remaining balance ({outstanding:.2f})."
            )

        new_status = "Paid" if payment["amount"] == outstanding else "Partially Paid"
        cur.execute(
            "UPDATE payments SET payment_status = %s WHERE payment_id = %s",
            (new_status, payment_id),
        )
        return new_status


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def get_reports_data():
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE payment_status IN ('Paid', 'Partially Paid')) AS total_revenue,
                (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE payment_status = 'Pending') AS pending_revenue,
                (SELECT COUNT(*) FROM bookings) AS total_bookings,
                (SELECT COUNT(*) FROM bookings WHERE booking_status = 'Confirmed') AS confirmed_bookings,
                (SELECT COUNT(*) FROM bookings WHERE booking_status = 'Checked-in') AS checked_in_bookings,
                (SELECT COUNT(*) FROM bookings WHERE booking_status = 'Checked-out') AS checked_out_bookings,
                (SELECT COUNT(*) FROM bookings WHERE booking_status = 'Cancelled') AS cancelled_bookings,
                (SELECT COUNT(*) FROM rooms) AS total_rooms,
                (SELECT COUNT(*) FROM rooms WHERE availability_status = 'Occupied') AS occupied_rooms,
                (SELECT COUNT(*) FROM rooms WHERE availability_status = 'Available') AS available_rooms,
                (SELECT COUNT(*) FROM rooms WHERE availability_status = 'Maintenance') AS maintenance_rooms
            """
        )
        summary = cur.fetchone()
        total_rooms = summary["total_rooms"] or 0
        summary["occupancy_rate"] = (
            round((summary["occupied_rooms"] / total_rooms) * 100, 1) if total_rooms > 0 else 0.0
        )

        cur.execute(
            """
            SELECT
                TO_CHAR(DATE_TRUNC('month', payment_date), 'YYYY-MM') AS month_key,
                TO_CHAR(DATE_TRUNC('month', payment_date), 'Mon YYYY') AS month_label,
                COUNT(*) AS payment_count,
                COALESCE(SUM(amount), 0) AS revenue
            FROM payments
            WHERE payment_status IN ('Paid', 'Partially Paid')
            GROUP BY 1, 2
            ORDER BY 1 DESC
            """
        )
        revenue_by_month = cur.fetchall()

        cur.execute(
            """
            SELECT
                r.room_type,
                COUNT(DISTINCT r.room_id) AS room_count,
                COUNT(b.booking_id) FILTER (WHERE b.booking_status != 'Cancelled') AS booking_count,
                COALESCE(SUM(b.num_nights) FILTER (WHERE b.booking_status != 'Cancelled'), 0) AS total_nights,
                COALESCE(SUM(b.total_amount) FILTER (WHERE b.booking_status != 'Cancelled'), 0) AS booked_value
            FROM rooms r
            LEFT JOIN bookings b ON b.room_id = r.room_id
            GROUP BY r.room_type
            ORDER BY booking_count DESC, booked_value DESC, r.room_type ASC
            """
        )
        room_type_stats = cur.fetchall()

        cur.execute(
            """
            SELECT
                c.customer_id,
                c.full_name,
                c.phone_number,
                COUNT(b.booking_id) AS total_bookings,
                COUNT(b.booking_id) FILTER (WHERE b.booking_status != 'Cancelled') AS valid_bookings,
                COALESCE(SUM(b.total_amount) FILTER (WHERE b.booking_status != 'Cancelled'), 0) AS total_billed,
                COALESCE(SUM(bp.paid_total) FILTER (WHERE b.booking_status != 'Cancelled'), 0) AS total_paid,
                MAX(b.check_in_date) AS last_check_in
            FROM customers c
            LEFT JOIN bookings b ON b.customer_id = c.customer_id
            LEFT JOIN (
                SELECT
                    booking_id,
                    SUM(CASE WHEN payment_status IN ('Paid', 'Partially Paid') THEN amount ELSE 0 END) AS paid_total
                FROM payments
                GROUP BY booking_id
            ) bp ON bp.booking_id = b.booking_id
            GROUP BY c.customer_id
            ORDER BY valid_bookings DESC, total_billed DESC, c.full_name ASC
            """
        )
        customer_history = cur.fetchall()

        cur.execute(
            """
            SELECT
                r.room_id,
                r.room_number,
                r.room_type,
                r.price_per_night,
                r.availability_status,
                cur_guest.booking_id AS active_booking_id,
                cur_guest.customer_name AS current_guest,
                cur_guest.check_out_date AS current_check_out,
                next_guest.check_in_date AS next_check_in
            FROM rooms r
            LEFT JOIN LATERAL (
                SELECT b.booking_id, c.full_name AS customer_name, b.check_out_date
                FROM bookings b
                JOIN customers c ON c.customer_id = b.customer_id
                WHERE b.room_id = r.room_id AND b.booking_status = 'Checked-in'
                ORDER BY b.check_in_date DESC
                LIMIT 1
            ) cur_guest ON TRUE
            LEFT JOIN LATERAL (
                SELECT b.check_in_date
                FROM bookings b
                WHERE b.room_id = r.room_id
                  AND b.booking_status = 'Confirmed'
                  AND b.check_out_date >= CURRENT_DATE
                ORDER BY b.check_in_date ASC
                LIMIT 1
            ) next_guest ON TRUE
            ORDER BY r.room_number ASC
            """
        )
        room_occupancy = cur.fetchall()

    return {
        "summary": summary,
        "revenue_by_month": revenue_by_month,
        "room_type_stats": room_type_stats,
        "customer_history": customer_history,
        "room_occupancy": room_occupancy,
    }
