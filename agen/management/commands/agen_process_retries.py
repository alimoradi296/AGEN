"""
agen_process_retries — enqueue Celery tasks for ready RetryInstructions.

Run every 30 seconds::

    * * * * * cd /app && python manage.py agen_process_retries

The task's Phase 0 will atomically claim the instruction; this command does
NOT mark instructions as processed (preventing double-claim on broker loss).
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Enqueue Celery tasks for RetryInstructions ready to execute."

    def handle(self, *args, **options):
        from django.conf import settings
        from django.db import transaction

        from agen.models import OutboxMessage, RetryInstruction

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        now = timezone.now()
        count = 0

        # Collect PKs without a lock first.
        ready_pks = list(
            RetryInstruction.objects.using(db_alias)
            .filter(processed=False, retry_at__lte=now)
            .values_list("pk", flat=True)
        )

        # Process each retry in its own transaction so a failure on one
        # doesn't block others, and so _notify_outbox fires per-commit.
        for pk in ready_pks:
            try:
                with transaction.atomic(using=db_alias):
                    try:
                        ri = RetryInstruction.objects.select_for_update(
                            skip_locked=True
                        ).get(pk=pk, processed=False)
                    except RetryInstruction.DoesNotExist:
                        continue  # already claimed

                    OutboxMessage.objects.using(db_alias).create(
                        instance=ri.instance,
                        step_name=ri.step_name,
                        extra={"retry_attempt": ri.attempt},
                    )
                    count += 1
                    logger.info(
                        "Enqueued retry for workflow %s step %r attempt %d",
                        ri.instance_id,
                        ri.step_name,
                        ri.attempt,
                    )

                    from agen.engine import _notify_outbox
                    _notify_outbox()

            except Exception:
                logger.warning("Error enqueuing retry pk=%s", pk, exc_info=True)

        msg = f"agen_process_retries: enqueued {count} retry task(s)."
        self.stdout.write(msg)
