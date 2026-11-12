-- Initial sample records for Hotel Management System

BEGIN;

-- Default staff / administrator accounts
-- admin / admin123, reception / staff123
INSERT INTO admins (username, password_hash, full_name)
VALUES
    (
        'admin',
        'pbkdf2:sha256:1000000$53kZgDlGJfMxNcv4$06f4f5a967f0d75e87cc4903ea149a4102cd7c688639a3f39e7818db03bdd786',
        'Hotel Administrator'
    ),
    (
        'reception',
        'pbkdf2:sha256:1000000$tqDMED4aA00bVa2K$22ef537aa14b4124dbded3ae60c193abc9891e0fe853a0e4db00e0f185eeba1d',
        'Front Desk Staff'
    )
ON CONFLICT (username) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM rooms) THEN
        INSERT INTO rooms (room_id, room_number, room_type, price_per_night, availability_status, description)
        VALUES
            (1, '101', 'Single', 85.00, 'Occupied', 'Ground floor single room near main lobby with desk and fast Wi-Fi'),
            (2, '102', 'Single', 85.00, 'Available', 'Quiet courtyard-facing single room with work desk'),
            (3, '103', 'Single', 90.00, 'Available', 'Corner single room with natural lighting and reading nook'),
            (4, '201', 'Double', 135.00, 'Occupied', 'Second floor double room with twin beds and seating area'),
            (5, '202', 'Double', 135.00, 'Available', 'King double room overlooking the garden courtyard'),
            (6, '203', 'Double', 140.00, 'Maintenance', 'Scheduled HVAC servicing and deep cleaning'),
            (7, '301', 'Deluxe', 210.00, 'Occupied', 'Spacious deluxe room with lounge seating and coffee bar'),
            (8, '302', 'Deluxe', 210.00, 'Available', 'Upper floor deluxe room with private balcony'),
            (9, '401', 'Suite', 340.00, 'Available', 'Executive suite with separate living room and conference desk'),
            (10, '402', 'Suite', 360.00, 'Occupied', 'Corner presidential suite with dining area and panoramic view');

        INSERT INTO customers (customer_id, full_name, phone_number, email, address)
        VALUES
            (1, 'Aarav Mehta', '+91-98401-22310', 'aarav.mehta@example.com', '14 Cathedral Road, Chennai, TN 600086'),
            (2, 'Priya Nair', '+91-98402-33421', 'priya.nair@example.com', '28 MG Road, Bengaluru, KA 560001'),
            (3, 'Vikram Sharma', '+91-98403-44532', 'vikram.sharma@example.com', '7 Park Street, Kolkata, WB 700016'),
            (4, 'Elena Rostova', '+1-415-555-0148', 'elena.rostova@example.com', '410 Market Street, San Francisco, CA 94105'),
            (5, 'Daniel Brooks', '+1-212-555-0193', 'daniel.brooks@example.com', '88 Madison Ave, New York, NY 10016'),
            (6, 'Meera Krishnan', '+91-98405-66754', 'meera.krishnan@example.com', '52 Besant Nagar, Chennai, TN 600090'),
            (7, 'Karthik Subramanian', '+91-98406-77865', 'karthik.subramanian@example.com', '19 Anna Salai, Madurai, TN 625001'),
            (8, 'Hannah Lim', '+65-9123-4567', 'hannah.lim@example.com', '12 Orchard Turn, Singapore 238801');

        INSERT INTO bookings (booking_id, customer_id, room_id, check_in_date, check_out_date, num_nights, total_amount, booking_status, notes)
        VALUES
            -- Completed stays across recent months
            (1, 1, 1, CURRENT_DATE - INTERVAL '48 days', CURRENT_DATE - INTERVAL '45 days', 3, 255.00, 'Checked-out', 'Early airport arrival, smooth stay'),
            (2, 2, 4, CURRENT_DATE - INTERVAL '42 days', CURRENT_DATE - INTERVAL '39 days', 3, 405.00, 'Checked-out', 'Corporate booking'),
            (3, 3, 7, CURRENT_DATE - INTERVAL '25 days', CURRENT_DATE - INTERVAL '22 days', 3, 630.00, 'Checked-out', 'Requested extra pillows'),
            (4, 4, 9, CURRENT_DATE - INTERVAL '18 days', CURRENT_DATE - INTERVAL '15 days', 3, 1020.00, 'Checked-out', 'Executive conference guest'),
            (5, 1, 8, CURRENT_DATE - INTERVAL '8 days', CURRENT_DATE - INTERVAL '5 days', 3, 630.00, 'Checked-out', 'Returning guest'),
            -- Active Checked-in stays (rooms 101, 301 due out today; 201, 402 active)
            (6, 5, 1, CURRENT_DATE - INTERVAL '3 days', CURRENT_DATE, 3, 255.00, 'Checked-in', 'Due for checkout today'),
            (7, 6, 7, CURRENT_DATE - INTERVAL '2 days', CURRENT_DATE, 2, 420.00, 'Checked-in', 'Morning checkout requested'),
            (8, 7, 4, CURRENT_DATE - INTERVAL '1 day', CURRENT_DATE + INTERVAL '2 days', 3, 405.00, 'Checked-in', 'Twin setup confirmed'),
            (9, 8, 10, CURRENT_DATE - INTERVAL '1 day', CURRENT_DATE + INTERVAL '3 days', 4, 1440.00, 'Checked-in', 'VIP corporate suite stay'),
            -- Confirmed stays (102, 202 arriving today; 302 upcoming)
            (10, 2, 2, CURRENT_DATE, CURRENT_DATE + INTERVAL '2 days', 2, 170.00, 'Confirmed', 'Expected arrival 2:00 PM'),
            (11, 3, 5, CURRENT_DATE, CURRENT_DATE + INTERVAL '3 days', 3, 405.00, 'Confirmed', 'Garden view requested'),
            (12, 4, 8, CURRENT_DATE + INTERVAL '4 days', CURRENT_DATE + INTERVAL '7 days', 3, 630.00, 'Confirmed', 'Airport pickup arranged'),
            -- Cancelled reservation
            (13, 5, 3, CURRENT_DATE + INTERVAL '6 days', CURRENT_DATE + INTERVAL '8 days', 2, 180.00, 'Cancelled', 'Cancelled by guest prior to arrival');

        INSERT INTO payments (payment_id, booking_id, amount, payment_date, payment_method, payment_status, notes)
        VALUES
            (1, 1, 255.00, (CURRENT_DATE - INTERVAL '45 days')::date, 'Card', 'Paid', 'Settled at checkout'),
            (2, 2, 405.00, (CURRENT_DATE - INTERVAL '39 days')::date, 'UPI', 'Paid', 'Corporate UPI transfer'),
            (3, 3, 630.00, (CURRENT_DATE - INTERVAL '22 days')::date, 'Card', 'Paid', 'Full payment on departure'),
            (4, 4, 1020.00, (CURRENT_DATE - INTERVAL '15 days')::date, 'Card', 'Paid', 'Visa corporate card'),
            (5, 5, 630.00, (CURRENT_DATE - INTERVAL '5 days')::date, 'Cash', 'Paid', 'Settled in cash'),
            (6, 6, 100.00, (CURRENT_DATE - INTERVAL '3 days')::date, 'UPI', 'Partially Paid', 'Advance deposit at check-in'),
            (7, 7, 420.00, (CURRENT_DATE - INTERVAL '2 days')::date, 'Card', 'Paid', 'Pre-paid in full at arrival'),
            (8, 8, 200.00, (CURRENT_DATE - INTERVAL '1 day')::date, 'Cash', 'Partially Paid', 'Initial cash deposit'),
            (9, 9, 1440.00, (CURRENT_DATE - INTERVAL '1 day')::date, 'Card', 'Pending', 'Invoice awaiting final card authorization'),
            (10, 10, 85.00, CURRENT_DATE, 'UPI', 'Partially Paid', 'First night booking advance');

        PERFORM setval(pg_get_serial_sequence('rooms', 'room_id'), COALESCE((SELECT MAX(room_id) FROM rooms), 1));
        PERFORM setval(pg_get_serial_sequence('customers', 'customer_id'), COALESCE((SELECT MAX(customer_id) FROM customers), 1));
        PERFORM setval(pg_get_serial_sequence('bookings', 'booking_id'), COALESCE((SELECT MAX(booking_id) FROM bookings), 1));
        PERFORM setval(pg_get_serial_sequence('payments', 'payment_id'), COALESCE((SELECT MAX(payment_id) FROM payments), 1));
    END IF;
END $$;

COMMIT;
