"""
Functional tests for run_workflow_sync.

These use a real database but no Celery/Redis.
"""
import pytest

from agen.models import StepExecution, WorkflowInstance
from agen.registry import register
from agen.testing import run_workflow_sync
from agen.workflow import BaseWorkflow, RetryPolicy, WaitHuman, WaitTimer, WorkflowDefinitionError


# ---------------------------------------------------------------------------
# Workflow fixtures
# ---------------------------------------------------------------------------

@register
class SimpleWorkflow(BaseWorkflow):
    def step_start(self):
        self.state["visited"] = ["start"]
        return "middle"

    def step_middle(self):
        self.state["visited"].append("middle")
        return "end"

    def step_end(self):
        self.state["visited"].append("end")
        return None


@register
class BranchingWorkflow(BaseWorkflow):
    def step_start(self):
        if self.state.get("go_left"):
            return "left"
        return "right"

    def step_left(self):
        self.state["branch"] = "left"
        return None

    def step_right(self):
        self.state["branch"] = "right"
        return None


@register
class TimerWorkflow(BaseWorkflow):
    def step_start(self):
        raise WaitTimer(seconds=60, then_step="resume")

    def step_resume(self):
        return None


@register
class HumanWorkflow(BaseWorkflow):
    def step_start(self):
        raise WaitHuman(title="Approve it", assigned_role="manager", then_step="approved")

    def step_approved(self):
        return None


@register
class FailingWorkflow(BaseWorkflow):
    def step_start(self):
        raise ValueError("deliberate failure")


class BadThenStepWorkflow(BaseWorkflow):
    """Not registered — used only to test compile-time validation at class definition."""
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_simple_sequential_flow():
    instance = run_workflow_sync("SimpleWorkflow", {"x": 1})
    assert instance.status == WorkflowInstance.STATUS_COMPLETED
    assert instance.state["visited"] == ["start", "middle", "end"]


@pytest.mark.django_db
def test_branching_left():
    instance = run_workflow_sync("BranchingWorkflow", {"go_left": True})
    assert instance.status == WorkflowInstance.STATUS_COMPLETED
    assert instance.state["branch"] == "left"


@pytest.mark.django_db
def test_branching_right():
    instance = run_workflow_sync("BranchingWorkflow", {})
    assert instance.status == WorkflowInstance.STATUS_COMPLETED
    assert instance.state["branch"] == "right"


@pytest.mark.django_db
def test_wait_timer_raises():
    with pytest.raises(WaitTimer):
        run_workflow_sync("TimerWorkflow")


@pytest.mark.django_db
def test_wait_human_raises():
    with pytest.raises(WaitHuman):
        run_workflow_sync("HumanWorkflow")


@pytest.mark.django_db
def test_failing_step_raises():
    with pytest.raises(ValueError, match="deliberate failure"):
        run_workflow_sync("FailingWorkflow")


@pytest.mark.django_db
def test_step_executions_created():
    instance = run_workflow_sync("SimpleWorkflow")
    steps = StepExecution.objects.filter(instance=instance).order_by("id")
    names = [s.step_name for s in steps]
    assert names == ["start", "middle", "end"]
    assert all(s.status == StepExecution.STATUS_SUCCEEDED for s in steps)


@pytest.mark.django_db
def test_initial_state_passed_through():
    instance = run_workflow_sync("SimpleWorkflow", {"user_id": 42})
    assert instance.state["user_id"] == 42


@pytest.mark.django_db
def test_mock_steps():
    calls = []

    def mock_start(workflow_obj):
        calls.append("mock_start")
        # Must initialise "visited" because step_middle and step_end read it.
        workflow_obj.state["visited"] = ["mocked_start"]
        workflow_obj.state["mocked"] = True
        return "middle"

    instance = run_workflow_sync(
        "SimpleWorkflow",
        {},
        mock_steps={"start": mock_start},
    )
    assert "mock_start" in calls
    assert instance.state.get("mocked") is True
    assert instance.state["visited"][0] == "mocked_start"


def test_compile_time_validation_bad_retry_key():
    with pytest.raises(WorkflowDefinitionError):
        class BrokenWorkflow(BaseWorkflow):
            retry_policy = {"nonexistent_step": RetryPolicy()}

            def step_start(self):
                return None
