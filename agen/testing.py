"""
Synchronous functional test helper.

``run_workflow_sync`` runs a workflow entirely in-process without Celery,
the outbox dispatcher, or any background processes.  It is the fastest way
to test the core step flow and signal handling.

Limitations (by design):
- Does NOT simulate timers (WaitTimer raises and the test must handle it).
- Does NOT simulate human tasks (WaitHuman raises and the test must handle it).
- Does NOT simulate retries (exceptions propagate to the caller).
- Does NOT use the outbox mechanism; next steps are called directly.
"""
from __future__ import annotations

import copy
import logging
from typing import Callable

from django.db import transaction
from django.utils import timezone

from agen.models import StepExecution, WorkflowInstance
from agen.registry import get_workflow_class
from agen.workflow import WorkflowDefinitionError, WorkflowSignal

logger = logging.getLogger(__name__)

MAX_STEPS = 1000  # guard against infinite loops


def run_workflow_sync(
    workflow_name: str,
    initial_state: dict | None = None,
    mock_steps: dict[str, Callable] | None = None,
) -> WorkflowInstance:
    """Run a workflow synchronously in-process.

    Parameters
    ----------
    workflow_name:
        The registered name of the workflow class.
    initial_state:
        Initial state dict for the workflow.  Defaults to empty dict.
    mock_steps:
        Optional mapping of step_name → callable.  When provided, the
        callable is used instead of the real step method.  Useful for
        isolating a step under test.

    Returns
    -------
    WorkflowInstance
        The final instance (status may be "completed", "failed", or a
        suspended status if a WorkflowSignal was raised).

    Raises
    ------
    WorkflowSignal
        If a step raises WaitTimer or WaitHuman.  The test must handle it.
    Exception
        Any exception raised by a step method (including business errors and
        infrastructure errors — none are caught here).
    """
    workflow_cls = get_workflow_class(workflow_name)
    state = copy.deepcopy(initial_state or {})

    instance = WorkflowInstance.objects.create(
        workflow_name=workflow_name,
        status=WorkflowInstance.STATUS_RUNNING,
        current_step="start",
        state=state,
    )

    step_name = "start"
    steps_run = 0

    while step_name is not None:
        if steps_run >= MAX_STEPS:
            raise RuntimeError(
                f"run_workflow_sync: exceeded {MAX_STEPS} steps — "
                "possible infinite loop."
            )
        steps_run += 1

        step_method_name = f"step_{step_name}"

        # Create a workflow instance bound to the current DB instance.
        workflow = workflow_cls(instance)

        StepExecution.objects.create(
            instance=instance,
            step_name=step_name,
            attempt=1,
            status=StepExecution.STATUS_RUNNING,
            input_state=copy.deepcopy(instance.state),
            started_at=timezone.now(),
        )

        # Resolve and call the step (mock or real).
        if mock_steps and step_name in mock_steps:
            result = mock_steps[step_name](workflow)
        else:
            step_fn = getattr(workflow_cls, step_method_name, None)
            if step_fn is None:
                instance.status = WorkflowInstance.STATUS_FAILED
                instance.save()
                raise WorkflowDefinitionError(
                    f"Step method {step_method_name!r} not found on "
                    f"{workflow_cls.__name__}."
                )
            bound_step = getattr(workflow, step_method_name)
            # Signals (WaitTimer, WaitHuman) propagate to the caller.
            result = bound_step()

        # Update step execution record.
        se = (
            StepExecution.objects.filter(instance=instance, step_name=step_name)
            .order_by("-attempt")
            .first()
        )
        if se:
            se.status = StepExecution.STATUS_SUCCEEDED
            se.finished_at = timezone.now()
            se.save()

        # Persist any state mutations from the step.
        instance.state = workflow.state
        instance.save()

        # Advance.
        if result is not None:
            if result not in workflow_cls._valid_steps:
                raise WorkflowDefinitionError(
                    f"Step {step_name!r} returned invalid next step {result!r}."
                )
            instance.current_step = result
            instance.status = WorkflowInstance.STATUS_RUNNING
            instance.save()
            step_name = result
        else:
            instance.status = WorkflowInstance.STATUS_COMPLETED
            instance.current_step = ""
            instance.save()
            step_name = None

    return instance
