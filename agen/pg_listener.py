"""
Dedicated psycopg2 connection for PostgreSQL LISTEN/NOTIFY.

This module provides a connection that is completely separate from Django's
ORM connection pool. The dispatcher uses this so Django can never swap out
the underlying connection without warning.
"""
from __future__ import annotations

import select

import psycopg2
import psycopg2.extensions


def make_listen_connection(dsn: str) -> psycopg2.extensions.connection:
    """Open a dedicated psycopg2 connection for LISTEN/NOTIFY.

    Must be separate from Django's ORM pool — never use this connection
    for regular queries.
    """
    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def wait_for_notify(conn: psycopg2.extensions.connection, timeout: float = 30.0) -> bool:
    """Block until a notification arrives or timeout expires.

    Returns True if notified, False on timeout.
    """
    r, _, _ = select.select([conn], [], [], timeout)
    if r:
        conn.poll()
        return True
    return False


def build_dsn(db_settings: dict) -> str:
    """Build a psycopg2 DSN string from a Django DATABASES entry."""
    parts = []
    if db_settings.get("HOST"):
        parts.append(f"host={db_settings['HOST']}")
    if db_settings.get("PORT"):
        parts.append(f"port={db_settings['PORT']}")
    if db_settings.get("NAME"):
        parts.append(f"dbname={db_settings['NAME']}")
    if db_settings.get("USER"):
        parts.append(f"user={db_settings['USER']}")
    if db_settings.get("PASSWORD"):
        parts.append(f"password={db_settings['PASSWORD']}")
    return " ".join(parts)
