"""
agen_collect_metrics — refresh Prometheus gauge metrics.

Run every 30s via Celery Beat or cron::

    python manage.py agen_collect_metrics

Requires: pip install agen[metrics]
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# Module-level gauge singletons (created once, reused on subsequent calls).
_gauges: dict = {}


def _get_gauges():
    global _gauges
    if _gauges:
        return _gauges
    from prometheus_client import Gauge
    _gauges = {
        "outbox_pending": Gauge(
            "agen_outbox_pending",
            "Number of undispatched outbox messages",
        ),
        "retry_pending": Gauge(
            "agen_retry_instructions_pending",
            "Number of unprocessed retry instructions",
        ),
        "human_pending": Gauge(
            "agen_human_tasks_pending",
            "Number of pending human tasks",
        ),
    }
    return _gauges


class Command(BaseCommand):
    help = "Refresh Agen Prometheus gauge metrics (requires agen[metrics])."

    def handle(self, *args, **options):
        try:
            _get_gauges()  # will raise ImportError if not installed
        except ImportError:
            self.stderr.write(
                "prometheus_client not installed. Run: pip install agen[metrics]"
            )
            return

        from django.conf import settings

        from agen.models import HumanTask, OutboxMessage, RetryInstruction

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        gauges = _get_gauges()

        outbox_count = OutboxMessage.objects.using(db_alias).filter(dispatched=False).count()
        retry_count = RetryInstruction.objects.using(db_alias).filter(processed=False).count()
        human_count = HumanTask.objects.using(db_alias).filter(status="pending").count()

        gauges["outbox_pending"].set(outbox_count)
        gauges["retry_pending"].set(retry_count)
        gauges["human_pending"].set(human_count)

        logger.info(
            "agen_collect_metrics: outbox=%d retries=%d human=%d",
            outbox_count,
            retry_count,
            human_count,
        )
        self.stdout.write(
            f"Metrics refreshed: outbox={outbox_count} retries={retry_count} human={human_count}"
        )
