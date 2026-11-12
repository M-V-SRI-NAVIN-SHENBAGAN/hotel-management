import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.environ.get(
        "FLASK_SECRET_KEY",
        "hotel-operations-internal-secret-change-in-production",
    )
    DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "5432"))
    DB_NAME = os.environ.get("DB_NAME", "hotel_management")
    DB_USER = os.environ.get("DB_USER", "postgres")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")

    # Optional embedded PostgreSQL fallback when no local PostgreSQL service is running
    ENABLE_EMBEDDED_PG = os.environ.get("ENABLE_EMBEDDED_PG", "auto").lower()
    EMBEDDED_PG_DATA = os.environ.get(
        "EMBEDDED_PG_DATA", "/tmp/hotel_management_pgdata"
    )
    AUTO_INIT_DB = os.environ.get("AUTO_INIT_DB", "1") == "1"

    HOTEL_NAME = os.environ.get("HOTEL_NAME", "Grandcrest Hotel")
    CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "$")
