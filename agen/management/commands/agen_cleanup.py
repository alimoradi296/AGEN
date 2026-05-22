"""
agen_cleanup — remove stale dispatched outbox messages.

Run daily::

    0 2 * * * cd /app && python manage.py agen_cleanup

Only deletes OutboxMessage rows where:
- dispatched = True
- The parent WorkflowInstance is in a terminal state (completed or failed)
- created_at < now - retention_period (default 30 days)

Preserves audit trails for in-flight workflows.
Use --force to also clean messages from non-terminal instances older than
the retention period (e.g. abandoned workflows).
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete old dispatched outbox messages (daily maintenance)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=30,
            help="Delete messages older than this many days (default: 30).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Also delete messages from non-terminal (e.g. abandoned) "
                "instances older than the retention period."
            ),
        )

    def handle(self, *args, **options):
        from django.conf import settings

        from agen.models import OutboxMessage, WorkflowInstance

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        retention = timedelta(days=options["retention_days"])
        cutoff = timezone.now() - retention

        qs = OutboxMessage.objects.using(db_alias).filter(
            dispatched=True,
            created_at__lt=cutoff,
        )

        if not options["force"]:
            qs = qs.filter(
                instance__status__in=list(WorkflowInstance.TERMINAL_STATUSES)
            )

        deleted, _ = qs.delete()
        msg = f"agen_cleanup: deleted {deleted} outbox message(s)."
        self.stdout.write(msg)
        logger.info(msg)
