-- PostgreSQL Schema for Hotel Management System

CREATE TABLE IF NOT EXISTS admins (
    admin_id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id SERIAL PRIMARY KEY,
    room_number VARCHAR(20) NOT NULL UNIQUE,
    room_type VARCHAR(20) NOT NULL CHECK (room_type IN ('Single', 'Double', 'Deluxe', 'Suite')),
    price_per_night NUMERIC(10, 2) NOT NULL CHECK (price_per_night > 0),
    availability_status VARCHAR(20) NOT NULL DEFAULT 'Available'
        CHECK (availability_status IN ('Available', 'Occupied', 'Maintenance')),
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    full_name VARCHAR(120) NOT NULL,
    phone_number VARCHAR(30) NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL UNIQUE,
    address TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    room_id INTEGER NOT NULL REFERENCES rooms(room_id) ON DELETE RESTRICT,
    check_in_date DATE NOT NULL,
    check_out_date DATE NOT NULL,
    num_nights INTEGER NOT NULL CHECK (num_nights > 0),
    total_amount NUMERIC(10, 2) NOT NULL CHECK (total_amount >= 0),
    booking_status VARCHAR(20) NOT NULL DEFAULT 'Confirmed'
        CHECK (booking_status IN ('Confirmed', 'Checked-in', 'Checked-out', 'Cancelled')),
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_check_out_after_check_in CHECK (check_out_date > check_in_date)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id SERIAL PRIMARY KEY,
    booking_id INTEGER NOT NULL REFERENCES bookings(booking_id) ON DELETE RESTRICT,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    payment_method VARCHAR(20) NOT NULL CHECK (payment_method IN ('Cash', 'Card', 'UPI')),
    payment_status VARCHAR(20) NOT NULL CHECK (payment_status IN ('Paid', 'Pending', 'Partially Paid')),
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rooms_type_status
    ON rooms (room_type, availability_status);

CREATE INDEX IF NOT EXISTS idx_bookings_room_dates
    ON bookings (room_id, check_in_date, check_out_date);

CREATE INDEX IF NOT EXISTS idx_bookings_customer
    ON bookings (customer_id);

CREATE INDEX IF NOT EXISTS idx_bookings_status
    ON bookings (booking_status);

CREATE INDEX IF NOT EXISTS idx_payments_booking
    ON payments (booking_id);

CREATE INDEX IF NOT EXISTS idx_payments_date_status
    ON payments (payment_date, payment_status);
