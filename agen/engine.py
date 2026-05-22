"""
Internal execution helpers.

These are called by tasks.py and are not part of the public API.
"""
from __future__ import annotations

import copy
import logging
import traceback
from datetime import timedelta
from typing import TYPE_CHECKING

import django.db
from django.db import transaction
from django.utils import timezone

from agen.models import (
    HumanTask,
    OutboxMessage,
    RetryInstruction,
    StepExecution,
    Timer,
    WorkflowInstance,
)
from agen.workflow import (
    InfrastructureError,
    WaitHuman,
    WaitTimer,
    WorkflowSignal,
    _RetrySignal,
)

if TYPE_CHECKING:
    from agen.workflow import BaseWorkflow, RetryPolicy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outbox notification
# ---------------------------------------------------------------------------

def _notify_outbox() -> None:
    """Schedule a PostgreSQL NOTIFY after the current transaction commits.

    If there is no active transaction (e.g. in tests with autocommit) the
    notification fires immediately.  The dispatcher is listening on the
    ``agen_outbox`` channel.
    """
    from django.conf import settings
    from django.db import connections

    db_alias = getattr(settings, "AGEN_DB_ALIAS", "default")

    def _send() -> None:
        try:
            conn = connections[db_alias]
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_notify('agen_outbox', '')")
        except Exception:
            # Non-fatal: the sweeper will pick up the message.
            logger.warning("Failed to send NOTIFY agen_outbox", exc_info=True)

    transaction.on_commit(_send)


# ---------------------------------------------------------------------------
# Infrastructure exception detection
# ---------------------------------------------------------------------------

def _is_infrastructure_exception(
    exc: Exception,
    extra_types: tuple,
) -> bool:
    """Return True if *exc* is a known infrastructure (transient) error."""
    from agen.workflow import DEFAULT_INFRASTRUCTURE_EXCEPTIONS

    all_types = DEFAULT_INFRASTRUCTURE_EXCEPTIONS + extra_types
    return isinstance(exc, all_types)


# ---------------------------------------------------------------------------
# Workflow / step failure helpers
# ---------------------------------------------------------------------------

def _fail_step(
    instance: WorkflowInstance,
    step_execution: StepExecution,
    exc: Exception,
) -> None:
    """Mark the step and workflow as failed; emit Django signals."""
    from agen import signals as agen_signals

    error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    step_execution.status = StepExecution.STATUS_FAILED
    step_execution.error = error_text
    step_execution.finished_at = timezone.now()
    step_execution.save()

    instance.status = WorkflowInstance.STATUS_FAILED
    instance.save()

    logger.error(
        "Step %r failed on instance %s (attempt %s): %s",
        step_execution.step_name,
        instance.id,
        step_execution.attempt,
        exc,
    )

    agen_signals.workflow_step_failed.send(
        sender=WorkflowInstance,
        instance=instance,
        step_execution=step_execution,
        error=exc,
    )
    agen_signals.workflow_failed.send(sender=WorkflowInstance, instance=instance)


def _fail_workflow(instance: WorkflowInstance, reason: str) -> None:
    """Unconditionally fail a workflow with a given reason string."""
    from agen import signals as agen_signals

    instance.status = WorkflowInstance.STATUS_FAILED
    instance.save()

    logger.error("Workflow %s failed: %s", instance.id, reason)

    agen_signals.workflow_failed.send(sender=WorkflowInstance, instance=instance)


# ---------------------------------------------------------------------------
# Signal application
# ---------------------------------------------------------------------------

def _apply_signal(
    instance: WorkflowInstance,
    sig: WorkflowSignal,
    step_execution: StepExecution,
) -> None:
    """Persist the side-effects of a WaitTimer or WaitHuman signal.

    Called inside the main atomic transaction, so everything is committed
    together with the step execution record.
    """
    from agen import signals as agen_signals

    step_execution.status = StepExecution.STATUS_SUCCEEDED
    step_execution.finished_at = timezone.now()
    step_execution.save()

    if isinstance(sig, WaitTimer):
        # Delete any existing timer for this instance (replace-on-raise
        # semantics: a step may be retried, re-creating the timer).
        Timer.objects.filter(instance=instance).delete()
        Timer.objects.create(
            instance=instance,
            deadline=sig.deadline,
            step_to_resume=sig.then_step,
        )
        instance.status = WorkflowInstance.STATUS_WAITING_TIMER
        instance.save()

        logger.info(
            "Workflow %s suspended: waiting for timer until %s (resume=%r)",
            instance.id,
            sig.deadline,
            sig.then_step,
        )
        agen_signals.workflow_suspended.send(
            sender=WorkflowInstance, instance=instance, signal_type="timer"
        )

    elif isinstance(sig, WaitHuman):
        HumanTask.objects.create(
            instance=instance,
            step_name=step_execution.step_name,
            title=sig.title,
            assigned_role=sig.assigned_role,
            then_step=sig.then_step,
        )
        instance.status = WorkflowInstance.STATUS_WAITING_HUMAN
        instance.save()

        logger.info(
            "Workflow %s suspended: waiting for human approval (title=%r, role=%r)",
            instance.id,
            sig.title,
            sig.assigned_role,
        )
        agen_signals.workflow_suspended.send(
            sender=WorkflowInstance, instance=instance, signal_type="human"
        )


# ---------------------------------------------------------------------------
# Retry handling (outside the main transaction)
# ---------------------------------------------------------------------------

def _handle_retry_signal(
    task,  # Celery task instance (for potential future use)
    signal: _RetrySignal,
    instance_id: str,
) -> None:
    """Create a durable RetryInstruction and outbox message.

    Called after the main step transaction has rolled back. Runs in its own
    transaction.  If the DB is unavailable, raises InfrastructureError so
    Celery retries the whole task.
    """
    try:
        with transaction.atomic():
            instance = WorkflowInstance.objects.select_for_update().get(id=instance_id)

            # Guard: if the workflow moved on (completed, failed, or changed
            # step) while we were rolling back, do nothing.
            if (
                instance.status in WorkflowInstance.TERMINAL_STATUSES
                or instance.current_step != signal.step_name
            ):
                logger.debug(
                    "Skipping retry setup for %s step %r — workflow moved on.",
                    instance_id,
                    signal.step_name,
                )
                return

            delay = signal.policy.compute_delay(signal.attempt)
            retry_at = timezone.now() + timedelta(seconds=delay)
            next_attempt = signal.attempt + 1

            error_text = "".join(
                traceback.format_exception(
                    type(signal.original_exception),
                    signal.original_exception,
                    signal.original_exception.__traceback__,
                )
            )

            # Serialize backoff config without exception types (not JSON-safe).
            backoff_config = {
                "max_attempts": signal.policy.max_attempts,
                "backoff_factor": signal.policy.backoff_factor,
                "initial_delay": signal.policy.initial_delay,
                "max_delay": signal.policy.max_delay,
            }

            RetryInstruction.objects.create(
                instance=instance,
                step_name=signal.step_name,
                attempt=next_attempt,
                max_attempts=signal.policy.max_attempts,
                retry_at=retry_at,
                backoff_config=backoff_config,
                error=error_text,
            )
            OutboxMessage.objects.create(
                instance=instance,
                step_name=signal.step_name,
                extra={"retry_attempt": next_attempt},
            )

            # Record retry metadata in workflow state for visibility.
            instance.state["_retry"] = {
                "step": signal.step_name,
                "attempt": next_attempt,
            }
            instance.save()

            _notify_outbox()

            logger.info(
                "Scheduled retry for workflow %s step %r: attempt %d at %s",
                instance_id,
                signal.step_name,
                next_attempt,
                retry_at,
            )

    except (django.db.OperationalError, django.db.InterfaceError) as exc:
        raise InfrastructureError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Workflow step step-success helper
# ---------------------------------------------------------------------------

def _complete_step(
    instance: WorkflowInstance,
    step_execution: StepExecution,
    next_step: str | None,
    workflow_cls: type["BaseWorkflow"],
) -> None:
    """Advance the workflow after a successful step.

    Creates an OutboxMessage for the next step (or marks the workflow
    completed) and fires Django signals.
    """
    from agen import signals as agen_signals

    step_execution.status = StepExecution.STATUS_SUCCEEDED
    step_execution.finished_at = timezone.now()
    step_execution.save()

    if next_step is not None:
        instance.current_step = next_step
        instance.status = WorkflowInstance.STATUS_RUNNING
        OutboxMessage.objects.create(instance=instance, step_name=next_step)
    else:
        instance.status = WorkflowInstance.STATUS_COMPLETED
        instance.current_step = ""

    instance.save()
    _notify_outbox()

    logger.info(
        "Step %r succeeded for workflow %s — next=%r",
        step_execution.step_name,
        instance.id,
        next_step,
    )

    agen_signals.workflow_step_succeeded.send(
        sender=WorkflowInstance,
        instance=instance,
        step_execution=step_execution,
    )
    if instance.status == WorkflowInstance.STATUS_COMPLETED:
        agen_signals.workflow_completed.send(
            sender=WorkflowInstance, instance=instance
        )
