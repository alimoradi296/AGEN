"""
agen_dispatcher — long-running outbox dispatcher process.

Connects to PostgreSQL, listens on the ``agen_outbox`` channel, and
dispatches undispatched OutboxMessage rows as Celery tasks.

Run as a separate, always-restarted service::

    python manage.py agen_dispatcher

One instance is sufficient per deployment. The sweeper (agen_sweep) acts as
a safety-net fallback.
"""
from __future__ import annotations

import logging
import select
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
POLL_TIMEOUT = 30        # seconds — block on select() for this long
RECONNECT_SLEEP = 5      # seconds after a DB error before reconnecting


class Command(BaseCommand):
    help = "Run the Agen outbox dispatcher (long-running process)."

    def handle(self, *args, **options):
        from django.conf import settings
        from django.db import connections

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")

        self.stdout.write("agen_dispatcher starting …")
        logger.info("agen_dispatcher starting, db_alias=%r", db_alias)

        last_heartbeat = time.monotonic()

        while True:
            try:
                conn = connections[db_alias]
                conn.ensure_connection()
                raw_conn = conn.connection  # psycopg2 connection

                # Switch to LISTEN mode. We need to use the raw psycopg2
                # connection for LISTEN/NOTIFY.
                raw_conn.set_isolation_level(0)  # autocommit
                with raw_conn.cursor() as cur:
                    cur.execute("LISTEN agen_outbox;")

                logger.info("agen_dispatcher listening on agen_outbox channel")
                self.stdout.write("Listening on agen_outbox …")

                while True:
                    now = time.monotonic()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                        logger.info("agen_dispatcher heartbeat — alive")
                        last_heartbeat = now

                    # Block waiting for a notification (or timeout).
                    r, _, _ = select.select([raw_conn], [], [], POLL_TIMEOUT)
                    raw_conn.poll()

                    # Process all pending notifications (or just sweep on timeout).
                    _drain_outbox(db_alias)

            except Exception as exc:
                logger.warning(
                    "agen_dispatcher encountered an error: %s — reconnecting in %ds",
                    exc,
                    RECONNECT_SLEEP,
                )
                try:
                    connections[db_alias].close()
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
