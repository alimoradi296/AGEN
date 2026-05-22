"""
agen_tick — process expired DB timers.

Run every 30 seconds::

    * * * * * cd /app && python manage.py agen_tick
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process expired workflow timers."

    def handle(self, *args, **options):
        from django.conf import settings
        from django.db import transaction

        from agen.engine import _notify_outbox
        from agen.models import OutboxMessage, Timer, WorkflowInstance

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        now = timezone.now()
        count = 0

        # Collect PKs of expired timers without a lock (outside any transaction).
        expired_pks = list(
            Timer.objects.using(db_alias)
            .filter(deadline__lte=now)
            .values_list("pk", flat=True)
        )

        # Process each timer in its own transaction so a failure on one
        # doesn't block others.
        for pk in expired_pks:
            try:
                with transaction.atomic(using=db_alias):
                    # Re-fetch with a lock inside the transaction.
                    try:
                        timer = Timer.objects.select_for_update(skip_locked=True).get(pk=pk)
                    except Timer.DoesNotExist:
                        continue  # already processed by another process

                    instance = WorkflowInstance.objects.select_for_update().get(
                        pk=timer.instance_id
                    )

                    if instance.status != WorkflowInstance.STATUS_WAITING_TIMER:
                        # Workflow moved on — stale timer, just delete it.
                        timer.delete()
                        continue

                    OutboxMessage.objects.create(
                        instance=instance,
                        step_name=timer.step_to_resume,
                    )
                    instance.status = WorkflowInstance.STATUS_RUNNING
                    instance.current_step = timer.step_to_resume
                    instance.save()
                    timer.delete()

                    _notify_outbox()

                    count += 1
                    logger.info(
                        "Timer fired for workflow %s — resuming at step %r",
                        instance.id,
                        timer.step_to_resume,
                    )
            except Exception:
                logger.warning("Error processing timer pk=%s", pk, exc_info=True)

        msg = f"agen_tick: processed {count} timer(s)."
        self.stdout.write(msg)
        if count:
            logger.info(msg)
