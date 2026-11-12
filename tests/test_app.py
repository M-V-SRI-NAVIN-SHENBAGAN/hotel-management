from datetime import date, timedelta
import re

import pytest

from app import create_app
from db import init_schema_and_seed


@pytest.fixture(autouse=True)
def reset_database():
    init_schema_and_seed(reset=True)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def login_as(client, username="admin", password="admin123"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_authentication_and_logout(client):
    # Protected route redirects to login
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    # Wrong credentials rejected
    bad = client.post(
        "/login",
        data={"username": "admin", "password": "wrongpassword"},
        follow_redirects=True,
    )
    assert bad.status_code == 200
    assert b"Invalid username or password." in bad.data

    # Valid login redirects to dashboard
    ok = login_as(client)
    assert ok.status_code == 302
    assert "/dashboard" in ok.headers["Location"]

    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert b"Total Rooms" in dash.data
    assert b"Today&#39;s Check-ins" in dash.data or b"Today's Check-ins" in dash.data
    assert b"$3,745.00" in dash.data

    # Logout clears session
    out = client.get("/logout", follow_redirects=False)
    assert out.status_code == 302
    assert "/login" in out.headers["Location"]
    after = client.get("/rooms", follow_redirects=False)
    assert after.status_code == 302
    assert "/login" in after.headers["Location"]


def test_room_crud_search_filter_and_delete_guard(client):
    login_as(client)

    # Filter by room type and availability
    resp = client.get("/rooms?room_type=Suite&availability=Available")
    assert resp.status_code == 200
    assert b"401" in resp.data
    assert b"402" not in resp.data  # 402 is Occupied

    # Search by room number
    resp = client.get("/rooms?q=203")
    assert b"203" in resp.data
    assert b"Maintenance" in resp.data

    # Create new room
    create_resp = client.post(
        "/rooms/new",
        data={
            "room_number": "501",
            "room_type": "Deluxe",
            "price_per_night": "250.00",
            "availability_status": "Available",
            "description": "Penthouse test room",
        },
        follow_redirects=True,
    )
    assert b"Room 501 added successfully." in create_resp.data
    assert b"501" in create_resp.data

    # Find created room id and edit it
    match = re.search(r"/rooms/(\d+)/edit", create_resp.data.decode("utf-8"))
    # Get exact room 501 edit link from row
    html = create_resp.data.decode("utf-8")
    m = re.search(r"501</strong>.*?href=\"/rooms/(\d+)/edit\"", html, re.S)
    assert m is not None
    room_501_id = int(m.group(1))

    edit_resp = client.post(
        f"/rooms/{room_501_id}/edit",
        data={
            "room_number": "501",
            "room_type": "Suite",
            "price_per_night": "310.00",
            "availability_status": "Available",
            "description": "Upgraded suite",
        },
        follow_redirects=True,
    )
    assert b"Room 501 updated." in edit_resp.data
    assert b"$310.00" in edit_resp.data

    # Delete newly created unused room succeeds
    del_ok = client.post(f"/rooms/{room_501_id}/delete", follow_redirects=True)
    assert b"Room deleted successfully." in del_ok.data

    # Delete room with bookings (Room 101, room_id=1) is prevented
    del_blocked = client.post("/rooms/1/delete", follow_redirects=True)
    assert b"Cannot delete Room 101" in del_blocked.data

    # Cannot change occupied room with active checked-in booking to Available manually
    occupied_edit = client.post(
        "/rooms/1/edit",
        data={
            "room_number": "101",
            "room_type": "Single",
            "price_per_night": "85.00",
            "availability_status": "Available",
            "description": "Attempt override",
        },
        follow_redirects=True,
    )
    assert b"must stay Occupied until check-out" in occupied_edit.data


def test_customer_management_and_history(client):
    login_as(client)

    # Register new customer
    resp = client.post(
        "/customers/new",
        data={
            "full_name": "Neha Kapoor",
            "phone_number": "+91-98765-00112",
            "email": "neha.kapoor@example.com",
            "address": "21 Marine Drive, Mumbai",
        },
        follow_redirects=True,
    )
    assert b"Customer registered successfully." in resp.data
    assert b"Neha Kapoor" in resp.data

    # Search customer by email/phone/name
    search_resp = client.get("/customers?q=98765-00112")
    assert b"Neha Kapoor" in search_resp.data

    # Duplicate phone fails cleanly
    dup_resp = client.post(
        "/customers/new",
        data={
            "full_name": "Another Person",
            "phone_number": "+91-98765-00112",
            "email": "another@example.com",
            "address": "Somewhere",
        },
        follow_redirects=True,
    )
    assert b"phone number already exists" in dup_resp.data

    # View existing customer history (#1 Aarav Mehta)
    hist = client.get("/customers/1")
    assert hist.status_code == 200
    assert b"Aarav Mehta" in hist.data
    assert b"#1" in hist.data


def test_booking_rules_overlap_and_transaction_rollback(client):
    login_as(client)
    today = date.today()

    # 1. Check-out must be after check-in
    bad_dates = client.post(
        "/bookings/new",
        data={
            "customer_mode": "existing",
            "customer_id": "1",
            "room_id": "3",  # Room 103 (Available, $90/night)
            "check_in_date": (today + timedelta(days=5)).isoformat(),
            "check_out_date": (today + timedelta(days=5)).isoformat(),
        },
        follow_redirects=True,
    )
    assert b"Check-out date must be after check-in date." in bad_dates.data

    # 2. Cannot book Occupied room (room_id=1) or Maintenance room (room_id=6)
    occ_resp = client.post(
        "/bookings/new",
        data={
            "customer_mode": "existing",
            "customer_id": "1",
            "room_id": "1",
            "check_in_date": (today + timedelta(days=10)).isoformat(),
            "check_out_date": (today + timedelta(days=12)).isoformat(),
        },
        follow_redirects=True,
    )
    assert b"Occupied and cannot be booked" in occ_resp.data

    maint_resp = client.post(
        "/bookings/new",
        data={
            "customer_mode": "existing",
            "customer_id": "1",
            "room_id": "6",
            "check_in_date": (today + timedelta(days=10)).isoformat(),
            "check_out_date": (today + timedelta(days=12)).isoformat(),
        },
        follow_redirects=True,
    )
    assert b"Maintenance and cannot be booked" in maint_resp.data

    # 3. Create valid booking on Room 103 (room_id=3, $90/night) for 3 nights
    ci = (today + timedelta(days=10)).isoformat()
    co = (today + timedelta(days=13)).isoformat()
    ok_resp = client.post(
        "/bookings/new",
        data={
            "customer_mode": "existing",
            "customer_id": "1",
            "room_id": "3",
            "check_in_date": ci,
            "check_out_date": co,
            "notes": "Quiet room",
        },
        follow_redirects=True,
    )
    assert b"created successfully" in ok_resp.data
    assert b"$270.00" in ok_resp.data  # 3 * 90.00

    # 4. Overlapping booking with inline new customer MUST fail AND roll back the customer creation!
    overlap_resp = client.post(
        "/bookings/new",
        data={
            "customer_mode": "new",
            "new_full_name": "Rollback Guest",
            "new_phone_number": "+1-555-0099",
            "new_email": "rollback@example.com",
            "new_address": "123 Rollback Lane",
            "room_id": "3",
            "check_in_date": (today + timedelta(days=11)).isoformat(),
            "check_out_date": (today + timedelta(days=14)).isoformat(),
        },
        follow_redirects=True,
    )
    assert b"already has an active reservation" in overlap_resp.data
    cust_check = client.get("/customers?q=rollback@example.com")
    assert b"Rollback Guest" not in cust_check.data  # Transaction rolled back!

    # 5. Back-to-back booking starting on checkout day (day 13 to day 15) IS allowed with inline new customer
    b2b_resp = client.post(
        "/bookings/new",
        data={
            "customer_mode": "new",
            "new_full_name": "Sanjay Verma",
            "new_phone_number": "+91-99000-11223",
            "new_email": "sanjay.verma@example.com",
            "new_address": "44 Anna Nagar, Chennai",
            "room_id": "3",
            "check_in_date": co,
            "check_out_date": (today + timedelta(days=15)).isoformat(),
        },
        follow_redirects=True,
    )
    assert b"created successfully" in b2b_resp.data
    assert b"Sanjay Verma" in b2b_resp.data
    assert b"$180.00" in b2b_resp.data  # 2 * 90.00


def test_checkin_checkout_respects_future_reservations(client):
    login_as(client)
    today = date.today()

    # Booking #10 is Confirmed today on Room 102 (room_id=2).
    # Create a future reservation on Room 102 BEFORE checking in #10.
    fut_ci = (today + timedelta(days=10)).isoformat()
    fut_co = (today + timedelta(days=12)).isoformat()
    fut = client.post(
        "/bookings/new",
        data={
            "customer_mode": "existing",
            "customer_id": "1",
            "room_id": "2",
            "check_in_date": fut_ci,
            "check_out_date": fut_co,
        },
        follow_redirects=True,
    )
    assert b"created successfully" in fut.data

    # Check in Booking #10 -> Room 102 becomes Occupied
    ci_resp = client.post("/bookings/10/checkin", follow_redirects=True)
    assert b"Room 102 is now Occupied." in ci_resp.data
    rooms_occ = client.get("/rooms?q=102")
    assert b"Occupied" in rooms_occ.data

    # Check out Booking #10 -> Room 102 becomes Available, future reservation still visible/protected
    co_resp = client.post("/bookings/10/checkout", follow_redirects=True)
    assert b"Room 102 is now Available for future bookings." in co_resp.data
    rooms_avail = client.get("/rooms?q=102")
    assert b"Available" in rooms_avail.data
    assert f"Reserved {fut_ci} to {fut_co}".encode() in rooms_avail.data

    # Future reservation still prevents overlapping booking!
    overlap = client.post(
        "/bookings/new",
        data={
            "customer_mode": "existing",
            "customer_id": "3",
            "room_id": "2",
            "check_in_date": (today + timedelta(days=11)).isoformat(),
            "check_out_date": (today + timedelta(days=13)).isoformat(),
        },
        follow_redirects=True,
    )
    assert b"already has an active reservation" in overlap.data


def test_payments_partial_pending_and_reports(client):
    login_as(client)
    today = date.today().isoformat()

    # Booking #6 (total $255.00, already $100.00 paid -> $155.00 outstanding)
    # Overpayment should fail
    over = client.post(
        "/payments/new",
        data={
            "booking_id": "6",
            "amount": "200.00",
            "payment_date": today,
            "payment_method": "Card",
            "payment_status": "Paid",
        },
        follow_redirects=True,
    )
    assert b"exceeds the remaining balance" in over.data

    # Partial payment of $55.00 -> auto-recorded as Partially Paid, leaving $100.00 balance
    part = client.post(
        "/payments/new",
        data={
            "booking_id": "6",
            "amount": "55.00",
            "payment_date": today,
            "payment_method": "Cash",
            "payment_status": "Paid",
        },
        follow_redirects=True,
    )
    assert b"Partially Paid" in part.data
    assert b"$100.00" in part.data

    # Final $100.00 payment -> Paid, $0.00 remaining
    final = client.post(
        "/payments/new",
        data={
            "booking_id": "6",
            "amount": "100.00",
            "payment_date": today,
            "payment_method": "UPI",
            "payment_status": "Paid",
        },
        follow_redirects=True,
    )
    assert b"(Paid)" in final.data
    assert b"$0.00" in final.data

    # Mark pending Payment #9 ($1440.00) as Paid
    mark = client.post("/payments/9/mark-paid", follow_redirects=True)
    assert b"updated to Paid" in mark.data

    # Verify Reports page loads with dynamic figures and zero technical/SQL leakage
    rep = client.get("/reports")
    assert rep.status_code == 200
    assert b"Revenue by Month" in rep.data
    assert b"Most Frequently Booked Room Types" in rep.data
    assert b"Current Room Occupancy" in rep.data
    assert b"Customer Booking History Summary" in rep.data
    assert b"$5,340.00" in rep.data  # 3745 + 55 + 100 + 1440 = 5340.00

    # Ensure no SQL/technical terms leak in any major rendered page
    for path in ["/dashboard", "/rooms", "/customers", "/bookings", "/checkin-checkout", "/payments", "/reports"]:
        page_html = client.get(path).data.decode("utf-8").lower()
        assert "select *" not in page_html
        assert "postgresql" not in page_html
        assert "psycopg2" not in page_html


def test_edit_cancel_booking_and_api_availability(client):
    login_as(client)
    today = date.today()

    # Check /api/rooms/available excludes Room 302 (room_id=8) during days +4..+7 (Booking #12)
    api_resp = client.get(
        f"/api/rooms/available?check_in={(today + timedelta(days=5)).isoformat()}&check_out={(today + timedelta(days=6)).isoformat()}"
    )
    assert api_resp.status_code == 200
    room_numbers = [r["room_number"] for r in api_resp.get_json()["rooms"]]
    assert "302" not in room_numbers
    assert "103" in room_numbers

    # Edit Booking #12 (currently Room 302, +4..+7) -> move to Room 401 (room_id=9, $340/night) for 4 nights
    edit_resp = client.post(
        "/bookings/12/edit",
        data={
            "room_id": "9",
            "check_in_date": (today + timedelta(days=4)).isoformat(),
            "check_out_date": (today + timedelta(days=8)).isoformat(),
            "booking_status": "Confirmed",
            "notes": "Upgraded to Suite 401",
        },
        follow_redirects=True,
    )
    assert b"Booking #12 updated." in edit_resp.data
    assert b"Room 401" in edit_resp.data
    assert b"$1,360.00" in edit_resp.data  # 4 * 340.00

    # Cancel a Checked-in booking (#8 on Room 201) -> Room 201 becomes Available again
    cancel_resp = client.post("/bookings/8/cancel", follow_redirects=True)
    assert b"Booking #8 cancelled." in cancel_resp.data
    room_201 = client.get("/rooms?q=201")
    assert b"Available" in room_201.data

    # Cannot record payment for cancelled booking (#13 or #8)
    pay_cancelled = client.post(
        "/payments/new",
        data={
            "booking_id": "8",
            "amount": "50.00",
            "payment_date": today.isoformat(),
            "payment_method": "Cash",
            "payment_status": "Paid",
        },
        follow_redirects=True,
    )
    assert b"Cannot record payment for a cancelled booking." in pay_cancelled.data


def test_proxy_cookie_behavior_and_invalid_inputs(client):
    # Over HTTPS reverse proxy headers, cookie should include Secure and SameSite=None
    https_login = client.post(
        "/login",
        data={"username": "admin", "password": "admin123"},
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "5000-test.e2b.app"},
        follow_redirects=False,
    )
    assert https_login.status_code == 302
    set_cookie = https_login.headers.get("Set-Cookie", "")
    assert "Secure" in set_cookie
    assert "SameSite=None" in set_cookie

