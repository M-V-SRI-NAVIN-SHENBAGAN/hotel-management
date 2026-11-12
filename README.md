# Hotel Management Application

Internal hotel operations management application built with Python, Flask, and PostgreSQL (`psycopg2`).

## Features

- **Staff Authentication**: Secure login with hashed credentials (`werkzeug.security`) and session logout.
- **Dashboard**: Live overview of total, available, and occupied rooms, total customers, active bookings, today's check-ins/check-outs, and total collected revenue.
- **Room Management**: Add, edit, delete, search by room number, and filter by room type (`Single`, `Double`, `Deluxe`, `Suite`) or availability (`Available`, `Occupied`, `Maintenance`).
- **Customer Management**: Register, edit, search by name/phone/email, and view per-customer booking history.
- **Booking Management**: Create bookings with existing or newly registered customers, automatic night and total calculation, transactional overlap prevention, availability enforcement, editing, and cancellation.
- **Check-in / Check-out**: Front-desk workflow for arrivals and departures that updates booking and room statuses while preserving future reservations.
- **Payment Management**: Record `Cash`, `Card`, and `UPI` payments, track `Paid` / `Partially Paid` / `Pending` statuses, settle pending balances, and compute outstanding balances.
- **Reports**: Total revenue, monthly revenue breakdown, booking counts, most frequently booked room types, customer booking history summary, and current room occupancy.

## Project Structure

```
hotel-management/
├── app.py                  # Flask application and routes
├── config.py               # Environment configuration
├── db.py                   # PostgreSQL connection and transaction helpers
├── services.py             # Business logic and SQL operations
├── create_db.py            # Database creation, schema setup, and sample data script
├── sql/
│   ├── schema.sql          # PostgreSQL table definitions and constraints
│   └── seed.sql            # Sample operational records
├── templates/              # Jinja2 HTML templates
├── static/                 # Stylesheets and client-side JavaScript
├── tests/                  # Automated pytest suite
├── requirements.txt        # Python dependencies
└── .env.example            # Example environment variables
```

## Setup Instructions

### 1. Install Python Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure PostgreSQL

Ensure PostgreSQL is running and create a `.env` file from `.env.example`:

```bash
cp .env.example .env
```

Update `.env` with your PostgreSQL connection details (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, or `DATABASE_URL`) and a strong `FLASK_SECRET_KEY`.

### 3. Initialize Database & Sample Data

Run the setup script to create the database (if needed), create all tables from `sql/schema.sql`, and populate sample records from `sql/seed.sql`:

```bash
python3 create_db.py
```

To reset all tables back to the initial sample dataset:

```bash
python3 create_db.py --reset
```

*(Alternatively, after creating the database manually, you can execute `psql -d hotel_management -f sql/schema.sql` followed by `psql -d hotel_management -f sql/seed.sql`.)*

### 4. Run the Application

```bash
python3 app.py
```

By default, the server listens on `http://0.0.0.0:5000` (configurable via the `PORT` environment variable).

### Default Staff Credentials

| Username | Password | Role |
| --- | --- | --- |
| `admin` | `admin123` | Hotel Administrator |
| `reception` | `staff123` | Front Desk Staff |

## Running Tests

```bash
pytest -v
```
