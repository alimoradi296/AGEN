"""
agen_recover_stalled — detect and rescue stalled workflow instances.

Run every 1 minute::

    * * * * * cd /app && python manage.py agen_recover_stalled

A workflow is considered stalled if:
- status = "running"
- current_step is non-empty
- updated_at < now - grace_period
- No recent OutboxMessage OR RetryInstruction within the grace period

On detection, inserts a new OutboxMessage for current_step so the
dispatcher/sweeper will re-enqueue the task.
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Detect and re-enqueue stalled workflow instances."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace-minutes",
            type=int,
            default=None,
            help="Override default grace period in minutes.",
        )

    def handle(self, *args, **options):
        from datetime import timedelta

        from django.conf import settings
        from django.db import transaction

        from agen.engine import _notify_outbox
        from agen.models import OutboxMessage, WorkflowInstance
        from agen.registry import get_workflow_class

        db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")
        now = timezone.now()
        recovered = 0

        default_grace = timedelta(minutes=options["grace_minutes"] or 5)
        cutoff = now - default_grace

        # Collect candidate PKs outside any transaction.
        candidate_pks = list(
            WorkflowInstance.objects.using(db_alias)
            .filter(
                status=WorkflowInstance.STATUS_RUNNING,
                current_step__gt="",
                updated_at__lt=cutoff,
            )
            .exclude(outbox_messages__created_at__gte=cutoff)
            .exclude(retry_instructions__created_at__gte=cutoff)
            .distinct()
            .values_list("pk", flat=True)
        )

        for pk in candidate_pks:
            # Per-workflow grace period override check.
            try:
                instance_check = WorkflowInstance.objects.get(pk=pk)
            except WorkflowInstance.DoesNotExist:
                continue

            try:
                workflow_cls = get_workflow_class(instance_check.workflow_name)
                grace = workflow_cls.stalled_grace_period
            except KeyError:
                grace = default_grace

            if instance_check.updated_at > now - grace:
                continue

            try:
                with transaction.atomic(using=db_alias):
                    instance = WorkflowInstance.objects.select_for_update().get(pk=pk)
                    if instance.status != WorkflowInstance.STATUS_RUNNING:
                        continue

                    OutboxMessage.objects.create(
                        instance=instance,
                        step_name=instance.current_step,
                        extra={"recovery": True},
                    )
                    _notify_outbox()
                    recovered += 1
                    logger.warning(
                        "Stalled workflow %s (%s) step %r — inserted recovery message.",
                        instance.id,
                        instance.workflow_name,
                        instance.current_step,
                    )
            except Exception:
                logger.error(
                    "Error recovering stalled workflow %s", pk, exc_info=True
                )

        msg = f"agen_recover_stalled: recovered {recovered} workflow(s)."
        self.stdout.write(msg)
        if recovered:
            logger.info(msg)
