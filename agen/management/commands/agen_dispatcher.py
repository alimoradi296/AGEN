"""
agen_dispatcher — long-running outbox dispatcher process.

Connects to PostgreSQL via a dedicated psycopg2 connection (separate from
Django's ORM pool) and listens on the ``agen_outbox`` channel.  Dispatches
undispatched OutboxMessage rows as Celery tasks whenever notified.

Run as a separate, always-restarted service::

    python manage.py agen_dispatcher

One instance is sufficient per deployment. The sweeper (agen_sweep) acts as
a safety-net fallback.
"""
from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
POLL_TIMEOUT = 30        # seconds — block waiting for NOTIFY for this long
RECONNECT_SLEEP = 5      # seconds after a DB error before reconnecting


class Command(BaseCommand):
    help = "Run the Agen outbox dispatcher (long-running process)."

    def handle(self, *args, **options):
        from django.conf import settings

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        db_settings = settings.DATABASES[db_alias]

        self.stdout.write("agen_dispatcher starting …")
        logger.info("agen_dispatcher starting, db_alias=%r", db_alias)

        last_heartbeat = time.monotonic()

        while True:
            listen_conn = None
            try:
                from agen.pg_listener import build_dsn, make_listen_connection, wait_for_notify

                dsn = build_dsn(db_settings)
                listen_conn = make_listen_connection(dsn)

                with listen_conn.cursor() as cur:
                    cur.execute("LISTEN agen_outbox;")

                logger.info("agen_dispatcher listening on agen_outbox channel")
                self.stdout.write("Listening on agen_outbox …")

                while True:
                    now = time.monotonic()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                        logger.info("agen_dispatcher heartbeat — alive")
                        last_heartbeat = now

                    # Block until notified or timeout — then drain regardless.
                    wait_for_notify(listen_conn, timeout=POLL_TIMEOUT)
                    _drain_outbox(db_alias)

            except Exception as exc:
                logger.warning(
                    "agen_dispatcher encountered an error: %s — reconnecting in %ds",
                    exc,
                    RECONNECT_SLEEP,
                )
                if listen_conn is not None:
                    try:
                        listen_conn.close()
                    except Exception:
                        pass
                time.sleep(RECONNECT_SLEEP)


def _drain_outbox(db_alias: str) -> None:
    """Dispatch all undispatched OutboxMessage rows as Celery tasks."""
    from django.db import transaction

    from agen.models import OutboxMessage
    from agen.dispatch import dispatch_message

    try:
        with transaction.atomic(using=db_alias):
            messages = (
                OutboxMessage.objects.using(db_alias)
                .select_for_update(skip_locked=True)
                .filter(dispatched=False)
                .select_related("instance")
                .order_by("created_at")
            )
            for msg in messages:
                dispatch_message(msg)
                msg.dispatched = True
                msg.save()
    except Exception:
        logger.warning("Error draining outbox", exc_info=True)
