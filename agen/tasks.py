"""
Celery task: execute_workflow_step

Implements the two-phase claim algorithm from PRD section 5.4.

This module is discovered by Celery's autodiscover_tasks. The task is
defined as a shared_task so it works regardless of which Celery app is
used by the host project.
"""
from __future__ import annotations

import copy
import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from agen.engine import (
    _apply_signal,
    _complete_step,
    _fail_step,
    _fail_workflow,
    _handle_retry_signal,
    _is_infrastructure_exception,
)
from agen.models import (
    RetryInstruction,
    StepExecution,
    WorkflowInstance,
)
from agen.registry import get_workflow_class
from agen.workflow import (
    InfrastructureError,
    WaitHuman,
    WaitTimer,
    WorkflowDefinitionError,
    WorkflowSignal,
    _RetrySignal,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="agen.tasks.execute_workflow_step",
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(InfrastructureError,),
    max_retries=10,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def execute_workflow_step(self, instance_id, step_name, retry_attempt=None, **extra):
    """Execute one step of a workflow instance.

    Parameters
    ----------
    instance_id:
        UUID string of the WorkflowInstance.
    step_name:
        Name of the step to execute (without ``step_`` prefix).
    retry_attempt:
        If this is a retry, the attempt number (int). None for the first run.
    **extra:
        Additional kwargs from OutboxMessage.extra (e.g. ``recovery=True``).
    """
    from agen import signals as agen_signals

    # -------------------------------------------------------------------
    # Phase 0: If this is a retry attempt, atomically claim the
    # RetryInstruction in its own short-lived transaction so that even if
    # the subsequent step execution fails, this instruction is never
    # re-played.
    # -------------------------------------------------------------------
    if retry_attempt is not None:
        with transaction.atomic():
            ri = (
                RetryInstruction.objects.select_for_update(skip_locked=True)
                .filter(
                    instance_id=instance_id,
                    step_name=step_name,
                    attempt=retry_attempt,
                    processed=False,
                )
                .first()
            )
            if ri is None:
                # Already claimed by another worker (or doesn't exist).
                logger.debug(
                    "Phase 0: RetryInstruction for %s step %r attempt %d not found "
                    "or already claimed — skipping.",
                    instance_id,
                    step_name,
                    retry_attempt,
                )
                return
            ri.processed = True
            ri.save()

        logger.debug(
            "Phase 0: claimed RetryInstruction for %s step %r attempt %d",
            instance_id,
            step_name,
            retry_attempt,
        )

    # -------------------------------------------------------------------
    # Phase 1: Main workflow transaction.
    # -------------------------------------------------------------------
    try:
        with transaction.atomic():
            try:
                instance = WorkflowInstance.objects.select_for_update().get(
                    id=instance_id
                )
            except WorkflowInstance.DoesNotExist:
                logger.error(
                    "WorkflowInstance %s not found — task %r discarded.",
                    instance_id,
                    step_name,
                )
                return

            # Guard: terminal states — nothing to do.
            if instance.status in WorkflowInstance.TERMINAL_STATUSES:
                logger.info(
                    "Workflow %s is already in terminal state %r — "
                    "discarding duplicate task for step %r. "
                    "This is safe; it usually means a task was re-delivered after the workflow completed.",
                    instance_id,
                    instance.status,
                    step_name,
                )
                return

            # Guard: step mismatch or non-runnable status.
            if instance.current_step != step_name or instance.status not in (
                WorkflowInstance.STATUS_RUNNING,
                WorkflowInstance.STATUS_PENDING,
            ):
                logger.warning(
                    "Workflow %s: refusing to run step %r — "
                    "current_step=%r, status=%r. "
                    "Possible causes: duplicate delivery, stale task after recovery, "
                    "or the workflow was manually advanced. Task discarded.",
                    instance_id,
                    step_name,
                    instance.current_step,
                    instance.status,
                )
                return

            # Resolve workflow class.
            try:
                workflow_cls = get_workflow_class(instance.workflow_name)
            except KeyError as exc:
                _fail_workflow(instance, str(exc))
                return

            # Resolve step method.
            step_method_name = f"step_{step_name}"
            step_fn = getattr(workflow_cls, step_method_name, None)
            if step_fn is None:
                _fail_workflow(
                    instance,
                    f"Step method {step_method_name!r} not found on "
                    f"{workflow_cls.__name__}.",
                )
                return

            attempt = retry_attempt if retry_attempt is not None else 1

            step_execution = StepExecution.objects.create(
                instance=instance,
                step_name=step_name,
                attempt=attempt,
                status=StepExecution.STATUS_RUNNING,
                input_state=copy.deepcopy(instance.state),
                started_at=timezone.now(),
            )

            agen_signals.workflow_step_started.send(
                sender=WorkflowInstance,
                instance=instance,
                step_name=step_name,
                attempt=attempt,
            )

            workflow = workflow_cls(instance)
            bound_step = getattr(workflow, step_method_name)

            try:
                result = bound_step()

            except WorkflowSignal as sig:
                # Validate then_step at runtime.
                if isinstance(sig, (WaitTimer, WaitHuman)):
                    if sig.then_step not in workflow_cls._valid_steps:
                        raise WorkflowDefinitionError(
                            f"Invalid then_step {sig.then_step!r} in "
                            f"{workflow_cls.__name__}.{step_method_name}. "
                            f"Valid steps: {sorted(workflow_cls._valid_steps)}"
                        )
                _apply_signal(instance, sig, step_execution)
                return  # transaction commits here

            except InfrastructureError:
                raise  # re-raise → Celery autoretry

            except Exception as exc:
                if _is_infrastructure_exception(exc, workflow_cls.infrastructure_exceptions):
                    raise InfrastructureError(str(exc)) from exc

                # Business error: check retry policy.
                retry_policy = workflow_cls.retry_policy.get(step_name)
                if retry_policy and isinstance(exc, retry_policy.retryable_exceptions):
                    if attempt < retry_policy.max_attempts:
                        raise _RetrySignal(step_name, attempt, retry_policy, exc) from exc
                    else:
                        # Exhausted retries.
                        _fail_step(instance, step_execution, exc)
                        return
                else:
                    _fail_step(instance, step_execution, exc)
                    return

            else:
                # Step returned normally.
                next_step = result
                if next_step is not None:
                    if next_step not in workflow_cls._valid_steps:
                        raise WorkflowDefinitionError(
                            f"Step {step_name!r} returned invalid next step "
                            f"{next_step!r}. Valid steps: "
                            f"{sorted(workflow_cls._valid_steps)}"
                        )
                _complete_step(instance, step_execution, next_step, workflow_cls)
                return

    except _RetrySignal as retry_sig:
        # Main transaction has rolled back. Handle retry in a new transaction.
        _handle_retry_signal(self, retry_sig, instance_id)
