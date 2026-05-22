"""
Integration tests for the core execution engine.

These tests call execute_workflow_step directly (no broker) and exercise
the real DB path: locking, outbox creation, signal application, retries,
timers, and human task resolution.

Requirements: real PostgreSQL (same as functional tests).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from agen.models import (
    HumanTask,
    OutboxMessage,
    RetryInstruction,
    StepExecution,
    Timer,
    WorkflowInstance,
)
from agen.registry import register
from agen.tasks import execute_workflow_step
from agen.workflow import BaseWorkflow, RetryPolicy, WaitHuman, WaitTimer


# ---------------------------------------------------------------------------
# Workflow fixtures
# ---------------------------------------------------------------------------

@register
class LinearWorkflow(BaseWorkflow):
    """start → validate → finalize → done (completes)."""

    def step_start(self):
        self.state["log"] = ["start"]
        return "validate"

    def step_validate(self):
        self.state["log"].append("validate")
        return "finalize"

    def step_finalize(self):
        self.state["log"].append("finalize")
        return "done"

    def step_done(self):
        self.state["log"].append("done")
        return None


@register
class TimerIntegrationWorkflow(BaseWorkflow):
    """start suspends on a timer, resume completes."""

    def step_start(self):
        raise WaitTimer(seconds=3600, then_step="resume")

    def step_resume(self):
        self.state["resumed"] = True
        return None


@register
class HumanIntegrationWorkflow(BaseWorkflow):
    """start suspends on a human task."""

    def step_start(self):
        raise WaitHuman(
            title="Approve this",
            assigned_role="manager",
            then_step="approved",
        )

    def step_approved(self):
        self.state["approved"] = True
        return None


class _TransientError(Exception):
    pass


@register
class RetryIntegrationWorkflow(BaseWorkflow):
    """start fails twice then succeeds on third attempt."""

    retry_policy = {
        "start": RetryPolicy(
            max_attempts=3,
            initial_delay=0.0,
            retryable_exceptions=(_TransientError,),
        )
    }

    def step_start(self):
        attempt = self.state.get("_retry", {}).get("attempt", 1)
        if attempt < 3:
            raise _TransientError("not yet")
        self.state["succeeded"] = True
        return None


@register
class RetryExhaustWorkflow(BaseWorkflow):
    """start always fails — exhausts retries and fails the workflow."""

    retry_policy = {
        "start": RetryPolicy(
            max_attempts=2,
            initial_delay=0.0,
            retryable_exceptions=(_TransientError,),
        )
    }

    def step_start(self):
        raise _TransientError("always fails")


@register
class IdempotencyWorkflow(BaseWorkflow):
    """Used to test step-mismatch and terminal-state guards."""

    def step_start(self):
        self.state["ran"] = True
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instance(workflow_name, state=None, step="start", status=WorkflowInstance.STATUS_RUNNING):
    return WorkflowInstance.objects.create(
        workflow_name=workflow_name,
        status=status,
        current_step=step,
        state=state or {},
    )


def _run(instance, step="start", retry_attempt=None):
    """Call execute_workflow_step synchronously (eager, no broker)."""
    execute_workflow_step(
        instance_id=str(instance.id),
        step_name=step,
        retry_attempt=retry_attempt,
    )
    instance.refresh_from_db()


# ---------------------------------------------------------------------------
# Basic step execution
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_step_advances_workflow():
    instance = _make_instance("LinearWorkflow")
    _run(instance, "start")

    assert instance.status == WorkflowInstance.STATUS_RUNNING
    assert instance.current_step == "validate"


@pytest.mark.django_db(transaction=True)
def test_step_creates_outbox_message():
    instance = _make_instance("LinearWorkflow")
    _run(instance, "start")

    msgs = OutboxMessage.objects.filter(instance=instance)
    assert msgs.count() == 1
    assert msgs.first().step_name == "validate"


@pytest.mark.django_db(transaction=True)
def test_step_creates_step_execution():
    instance = _make_instance("LinearWorkflow")
    _run(instance, "start")

    se = StepExecution.objects.get(instance=instance, step_name="start")
    assert se.status == StepExecution.STATUS_SUCCEEDED
    assert se.finished_at is not None


@pytest.mark.django_db(transaction=True)
def test_full_linear_flow_completes():
    instance = _make_instance("LinearWorkflow")

    for step in ["start", "validate", "finalize", "done"]:
        instance.refresh_from_db()
        _run(instance, step)

    assert instance.status == WorkflowInstance.STATUS_COMPLETED
    assert instance.current_step == ""
    assert instance.state["log"] == ["start", "validate", "finalize", "done"]


# ---------------------------------------------------------------------------
# Idempotency guards
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_terminal_state_guard():
    """Task on a completed workflow is a no-op."""
    instance = _make_instance(
        "IdempotencyWorkflow",
        status=WorkflowInstance.STATUS_COMPLETED,
        step="",
    )
    _run(instance, "start")

    # Status must not change.
    assert instance.status == WorkflowInstance.STATUS_COMPLETED
    assert StepExecution.objects.filter(instance=instance).count() == 0


@pytest.mark.django_db(transaction=True)
def test_step_mismatch_guard():
    """Task for the wrong step is a no-op."""
    instance = _make_instance("IdempotencyWorkflow", step="start")
    # Run for a step that is NOT current_step.
    execute_workflow_step(
        instance_id=str(instance.id),
        step_name="nonexistent",
    )
    instance.refresh_from_db()

    assert instance.status == WorkflowInstance.STATUS_RUNNING
    assert instance.current_step == "start"
    assert StepExecution.objects.filter(instance=instance).count() == 0


# ---------------------------------------------------------------------------
# Timer suspension
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_wait_timer_suspends_workflow():
    instance = _make_instance("TimerIntegrationWorkflow")
    _run(instance, "start")

    assert instance.status == WorkflowInstance.STATUS_WAITING_TIMER
    assert Timer.objects.filter(instance=instance).count() == 1


@pytest.mark.django_db(transaction=True)
def test_agen_tick_fires_expired_timer():
    from django.core.management import call_command

    instance = _make_instance("TimerIntegrationWorkflow")
    _run(instance, "start")

    assert instance.status == WorkflowInstance.STATUS_WAITING_TIMER

    # Manually expire the timer by backdating the deadline.
    Timer.objects.filter(instance=instance).update(
        deadline=timezone.now() - timedelta(seconds=1)
    )

    call_command("agen_tick", verbosity=0)
    instance.refresh_from_db()

    assert instance.status == WorkflowInstance.STATUS_RUNNING
    assert instance.current_step == "resume"
    assert OutboxMessage.objects.filter(instance=instance, step_name="resume").exists()


@pytest.mark.django_db(transaction=True)
def test_agen_tick_ignores_future_timer():
    from django.core.management import call_command

    instance = _make_instance("TimerIntegrationWorkflow")
    _run(instance, "start")

    # Timer deadline is 1 hour in future — should NOT fire.
    call_command("agen_tick", verbosity=0)
    instance.refresh_from_db()

    assert instance.status == WorkflowInstance.STATUS_WAITING_TIMER


# ---------------------------------------------------------------------------
# Human task suspension and resolution
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_wait_human_suspends_workflow():
    instance = _make_instance("HumanIntegrationWorkflow")
    _run(instance, "start")

    assert instance.status == WorkflowInstance.STATUS_WAITING_HUMAN
    assert HumanTask.objects.filter(instance=instance).count() == 1


@pytest.mark.django_db(transaction=True)
def test_resolve_human_task_approve_resumes_workflow():
    from agen.human import resolve_human_task

    instance = _make_instance("HumanIntegrationWorkflow")
    _run(instance, "start")

    task = HumanTask.objects.get(instance=instance)
    resolve_human_task(task.id, approved=True, resolved_by="test_user")
    instance.refresh_from_db()

    assert instance.status == WorkflowInstance.STATUS_RUNNING
    assert instance.current_step == "approved"
    assert OutboxMessage.objects.filter(instance=instance, step_name="approved").exists()
    task.refresh_from_db()
    assert task.status == HumanTask.STATUS_APPROVED
    assert task.resolved_by == "test_user"


@pytest.mark.django_db(transaction=True)
def test_resolve_human_task_reject_fails_workflow():
    from agen.human import resolve_human_task

    instance = _make_instance("HumanIntegrationWorkflow")
    _run(instance, "start")

    task = HumanTask.objects.get(instance=instance)
    resolve_human_task(task.id, approved=False, resolved_by="test_user")
    instance.refresh_from_db()

    assert instance.status == WorkflowInstance.STATUS_FAILED
    task.refresh_from_db()
    assert task.status == HumanTask.STATUS_REJECTED


@pytest.mark.django_db(transaction=True)
def test_resolve_human_task_double_resolution_raises():
    from agen.human import resolve_human_task

    instance = _make_instance("HumanIntegrationWorkflow")
    _run(instance, "start")

    task = HumanTask.objects.get(instance=instance)
    resolve_human_task(task.id, approved=True, resolved_by="first")

    with pytest.raises(ValueError, match="already resolved"):
        resolve_human_task(task.id, approved=True, resolved_by="second")


# ---------------------------------------------------------------------------
# Business retries
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_retry_creates_retry_instruction_and_outbox():
    instance = _make_instance("RetryIntegrationWorkflow")
    _run(instance, "start")

    assert RetryInstruction.objects.filter(instance=instance).count() == 1
    ri = RetryInstruction.objects.get(instance=instance)
    assert ri.attempt == 2
    assert ri.processed is False

    assert OutboxMessage.objects.filter(instance=instance, step_name="start").exists()


@pytest.mark.django_db(transaction=True)
def test_agen_process_retries_enqueues_outbox():
    from django.core.management import call_command

    instance = _make_instance("RetryIntegrationWorkflow")
    _run(instance, "start")  # triggers retry → creates RetryInstruction

    # Backdate retry_at so it's ready now.
    RetryInstruction.objects.filter(instance=instance).update(
        retry_at=timezone.now() - timedelta(seconds=1)
    )
    # Clear the outbox from the first attempt so we can count cleanly.
    OutboxMessage.objects.filter(instance=instance).delete()

    call_command("agen_process_retries", verbosity=0)

    assert OutboxMessage.objects.filter(instance=instance, step_name="start").exists()


@pytest.mark.django_db(transaction=True)
def test_retry_exhaustion_fails_workflow():
    """When retries are exhausted the workflow moves to failed."""
    instance = _make_instance("RetryExhaustWorkflow")

    # First attempt: fails, creates RetryInstruction for attempt 2.
    _run(instance, "start", retry_attempt=None)
    assert instance.status == WorkflowInstance.STATUS_RUNNING

    ri = RetryInstruction.objects.get(instance=instance)
    assert ri.attempt == 2
    assert ri.processed is False

    # Second attempt (last allowed): pass retry_attempt so Phase 0 claims it.
    _run(instance, "start", retry_attempt=2)

    assert instance.status == WorkflowInstance.STATUS_FAILED
    se = StepExecution.objects.filter(instance=instance, step_name="start").order_by("-attempt").first()
    assert se.status == StepExecution.STATUS_FAILED


# ---------------------------------------------------------------------------
# Stalled recovery
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_agen_recover_stalled_inserts_outbox():
    from django.core.management import call_command

    instance = _make_instance("LinearWorkflow")

    # Backdate updated_at to simulate a stalled workflow.
    WorkflowInstance.objects.filter(pk=instance.pk).update(
        updated_at=timezone.now() - timedelta(minutes=10)
    )

    call_command("agen_recover_stalled", "--grace-minutes=5", verbosity=0)

    assert OutboxMessage.objects.filter(
        instance=instance, step_name="start"
    ).exists()
