"""
resolve_human_task — idempotent human-task resolution.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from agen.models import HumanTask, OutboxMessage, WorkflowInstance

logger = logging.getLogger(__name__)


def resolve_human_task(
    task_id: int,
    approved: bool,
    resolved_by: str = "",
) -> None:
    """Resolve a pending HumanTask.

    Parameters
    ----------
    task_id:
        Primary key of the HumanTask.
    approved:
        True → approve; the workflow resumes at ``then_step``.
        False → reject; the workflow transitions to ``failed``.
    resolved_by:
        Identifier of the person resolving the task (stored for audit).

    Raises
    ------
    HumanTask.DoesNotExist
        If the task_id is invalid.
    ValueError
        If the task is already resolved or the workflow is not in the
        expected state.
    """
    from agen import signals as agen_signals
    from agen.engine import _notify_outbox

    with transaction.atomic():
        # Lock task first, then instance (consistent order avoids deadlocks).
        task = HumanTask.objects.select_for_update().get(pk=task_id)

        if task.status != HumanTask.STATUS_PENDING:
            raise ValueError(
                f"HumanTask {task_id} is already resolved (status={task.status!r})."
            )

        instance = WorkflowInstance.objects.select_for_update().get(
            pk=task.instance_id
        )

        if instance.status != WorkflowInstance.STATUS_WAITING_HUMAN:
            raise ValueError(
                f"WorkflowInstance {instance.id} is not waiting for a human "
                f"(status={instance.status!r})."
            )

        task.resolved_by = resolved_by
        task.resolved_at = timezone.now()

        if approved:
            task.status = HumanTask.STATUS_APPROVED
            task.save()

            instance.status = WorkflowInstance.STATUS_RUNNING
            instance.current_step = task.then_step
            instance.save()

            OutboxMessage.objects.create(
                instance=instance,
                step_name=task.then_step,
            )
            _notify_outbox()

            logger.info(
                "HumanTask %s approved by %r — workflow %s resuming at step %r",
                task_id,
                resolved_by,
                instance.id,
                task.then_step,
            )
        else:
            task.status = HumanTask.STATUS_REJECTED
            task.save()

            instance.status = WorkflowInstance.STATUS_FAILED
            instance.save()

            logger.info(
                "HumanTask %s rejected by %r — workflow %s failed.",
                task_id,
                resolved_by,
                instance.id,
            )
            agen_signals.workflow_failed.send(
                sender=WorkflowInstance, instance=instance
            )
