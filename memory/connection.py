"""
Connection Manager — Async-safe PostgreSQL connection pool for the Memory Spine.

Uses psycopg (v3) with connection pooling optimised for 93-agent concurrency.
Falls back to psycopg2 if psycopg3 is unavailable.

Usage:
    from memory.connection import get_pool, release_pool
    pool = get_pool()
    async with pool.connection() as conn:
        ...
"""
import os
import logging
from contextlib import contextmanager

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection string from .env  (BRAIN_CONNECTION_STRING or BRAIN_DIRECT_URL)
# ---------------------------------------------------------------------------
def _get_connection_string() -> str:
    """Resolve the best available PostgreSQL connection string."""
    # Prefer pooler connection (BRAIN_CONNECTION_STRING) — reliable from any network
    conn = os.environ.get("BRAIN_CONNECTION_STRING", "")
    if conn:
        return conn
    # Fallback to direct URL (may not resolve from external networks)
    direct = os.environ.get("BRAIN_DIRECT_URL", "")
    if direct:
        return direct
    raise RuntimeError(
        "No database connection string found. "
        "Set BRAIN_DIRECT_URL or BRAIN_CONNECTION_STRING in .env"
    )


# ---------------------------------------------------------------------------
# Synchronous pool (psycopg2 — used by agent nodes which are sync)
# ---------------------------------------------------------------------------

def get_sync_connection():
    """
    Get a synchronous psycopg2 connection.
    Uses a simple connection (not pooled) for migration scripts and one-off queries.
    For production agent workloads, use get_pool() instead.
    """
    try:
        import psycopg2
        conn_str = _get_connection_string()
        conn = psycopg2.connect(conn_str)
        conn.autocommit = False
        return conn
    except ImportError:
        raise RuntimeError("psycopg2 is required. Install with: pip install psycopg2-binary")


# ---------------------------------------------------------------------------
# Async pool (psycopg3 — used for high-throughput agent memory ops)
# ---------------------------------------------------------------------------
_async_pool = None

def get_pool(min_size: int = 5, max_size: int = 20):
    """
    Get or create the async connection pool (psycopg3 ConnectionPool).

    Pool sizing rationale:
    - 93 agents, but they don't all hit memory simultaneously
    - Peak load estimated at ~30 concurrent memory operations
    - min_size=5 keeps warm connections ready
    - max_size=20 prevents overwhelming Supabase (max 60 connections)
    """
    global _async_pool
    if _async_pool is not None:
        return _async_pool

    try:
        from psycopg_pool import ConnectionPool
        conn_str = _get_connection_string()
        _async_pool = ConnectionPool(
            conninfo=conn_str,
            min_size=min_size,
            max_size=max_size,
            timeout=10.0,        # wait max 10s for a connection
            max_idle=300.0,      # close idle connections after 5 min
            max_lifetime=3600.0, # recycle connections after 1 hour
            kwargs={"autocommit": False},
        )
        log.info(f"memory.pool.created min_size={min_size} max_size={max_size}")
        return _async_pool
    except ImportError:
        log.warning("psycopg_pool not available, falling back to simple connections")
        return None


def release_pool():
    """Close the connection pool gracefully."""
    global _async_pool
    if _async_pool is not None:
        _async_pool.close()
        _async_pool = None
        log.info("memory.pool.closed")


# ---------------------------------------------------------------------------
# Context manager for simple sync operations
# ---------------------------------------------------------------------------
@contextmanager
def db_connection():
    """
    Context manager for synchronous database operations.
    Automatically commits on success, rolls back on exception.

    Usage:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """
    conn = get_sync_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
def check_connection() -> dict:
    """
    Test database connectivity and return status info.
    Returns: {"connected": bool, "version": str, "extensions": list}
    """
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]

                cur.execute(
                    "SELECT extname FROM pg_extension ORDER BY extname"
                )
                extensions = [row[0] for row in cur.fetchall()]

                return {
                    "connected": True,
                    "version": version,
                    "extensions": extensions,
                }
    except Exception as e:
        return {
            "connected": False,
            "version": None,
            "extensions": [],
            "error": str(e),
        }
