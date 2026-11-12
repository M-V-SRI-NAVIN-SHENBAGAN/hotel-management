import re
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from config import BASE_DIR, Config

_init_lock = threading.Lock()
_resolved_dsn = None
_schema_verified = False


def _validate_db_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Invalid database name: {name}")
    return name


def _build_dsn(database: str | None = None) -> str:
    if Config.DATABASE_URL:
        return Config.DATABASE_URL
    db_name = database or Config.DB_NAME
    return (
        f"dbname={db_name} user={Config.DB_USER} password={Config.DB_PASSWORD} "
        f"host={Config.DB_HOST} port={Config.DB_PORT}"
    )


def _should_allow_embedded_fallback() -> bool:
    if Config.ENABLE_EMBEDDED_PG in ("0", "false", "no", "off"):
        return False
    if Config.ENABLE_EMBEDDED_PG in ("1", "true", "yes", "on"):
        return True
    # 'auto': allow fallback only when configured host is local and no explicit remote URL
    if Config.DATABASE_URL:
        return False
    return Config.DB_HOST in ("localhost", "127.0.0.1", "")


def _start_embedded_pg_and_ensure_db(db_name: str) -> str:
    import pgserver

    pgdata = Path(Config.EMBEDDED_PG_DATA)
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(pgdata, cleanup_mode=None)
    maintenance_uri = server.get_uri("postgres")

    conn = psycopg2.connect(maintenance_uri)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone() is None:
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
                )
    finally:
        conn.close()

    return server.get_uri(db_name)


def resolve_dsn() -> str:
    global _resolved_dsn
    if _resolved_dsn is not None:
        return _resolved_dsn

    with _init_lock:
        if _resolved_dsn is not None:
            return _resolved_dsn

        primary_dsn = _build_dsn()
        if Config.ENABLE_EMBEDDED_PG in ("1", "true", "yes", "on"):
            db_name = _validate_db_name(Config.DB_NAME)
            _resolved_dsn = _start_embedded_pg_and_ensure_db(db_name)
            return _resolved_dsn

        try:
            conn = psycopg2.connect(primary_dsn, connect_timeout=2)
            conn.close()
            _resolved_dsn = primary_dsn
            return _resolved_dsn
        except psycopg2.OperationalError:
            if not _should_allow_embedded_fallback():
                raise
            try:
                import pgserver  # noqa: F401
            except ImportError:
                raise
            db_name = _validate_db_name(Config.DB_NAME)
            _resolved_dsn = _start_embedded_pg_and_ensure_db(db_name)
            return _resolved_dsn


def ensure_database_exists() -> str:
    """Create target database if using external PostgreSQL, or start embedded server."""
    global _resolved_dsn
    if Config.ENABLE_EMBEDDED_PG in ("1", "true", "yes", "on"):
        db_name = _validate_db_name(Config.DB_NAME)
        _resolved_dsn = _start_embedded_pg_and_ensure_db(db_name)
        return _resolved_dsn

    if Config.DATABASE_URL:
        conn = psycopg2.connect(Config.DATABASE_URL, connect_timeout=3)
        conn.close()
        _resolved_dsn = Config.DATABASE_URL
        return _resolved_dsn

    db_name = _validate_db_name(Config.DB_NAME)
    maintenance_dsn = _build_dsn(database="postgres")
    try:
        conn = psycopg2.connect(maintenance_dsn, connect_timeout=2)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
                )
                if cur.fetchone() is None:
                    cur.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
                    )
        finally:
            conn.close()
        _resolved_dsn = _build_dsn(database=db_name)
        return _resolved_dsn
    except psycopg2.OperationalError:
        if not _should_allow_embedded_fallback():
            raise
        _resolved_dsn = _start_embedded_pg_and_ensure_db(db_name)
        return _resolved_dsn


def _ensure_initialized(conn) -> None:
    global _schema_verified
    if _schema_verified or not Config.AUTO_INIT_DB:
        return

    with _init_lock:
        if _schema_verified:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.admins')")
            exists = cur.fetchone()[0]
        if exists is None:
            schema_sql = (BASE_DIR / "sql" / "schema.sql").read_text(encoding="utf-8")
            seed_sql = (BASE_DIR / "sql" / "seed.sql").read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(schema_sql)
                cur.execute(seed_sql)
            conn.commit()
        _schema_verified = True


def get_connection(ensure_init: bool = True):
    dsn = resolve_dsn()
    conn = psycopg2.connect(dsn)
    if ensure_init:
        _ensure_initialized(conn)
    return conn


@contextmanager
def db_cursor(commit: bool = False):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


@contextmanager
def transaction():
    with db_cursor(commit=True) as cur:
        yield cur


def init_schema_and_seed(reset: bool = False) -> None:
    global _schema_verified
    ensure_database_exists()
    conn = get_connection(ensure_init=False)
    try:
        schema_sql = (BASE_DIR / "sql" / "schema.sql").read_text(encoding="utf-8")
        seed_sql = (BASE_DIR / "sql" / "seed.sql").read_text(encoding="utf-8")
        with conn.cursor() as cur:
            if reset:
                cur.execute(
                    """
                    DROP TABLE IF EXISTS payments CASCADE;
                    DROP TABLE IF EXISTS bookings CASCADE;
                    DROP TABLE IF EXISTS customers CASCADE;
                    DROP TABLE IF EXISTS rooms CASCADE;
                    DROP TABLE IF EXISTS admins CASCADE;
                    """
                )
            cur.execute(schema_sql)
            cur.execute(seed_sql)
        conn.commit()
        _schema_verified = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
